#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""用户管理动作的业务编排：重置密码 / 重置 MFA / 模拟用户 / IM 身份代录。

视图层只保留请求解析、鉴权（含目标可变性校验）与响应构造；本模块承接动作内
的多步编排与副作用（站内信、审计留痕、会话与登录状态清理），业务码与文案
保持原有口径不变。
"""

import json
from typing import Any

from django.utils.translation import gettext_lazy as _

from audit.services import OperationLog
from identity.models import UserInfo, UserOAuthBinding
from identity.utils.impersonation import start_impersonation
from mfa.cache import UserConfirmStateCache
from mfa.services import clear_recovery_codes
from notifications.message import SiteMessageUtil
from settings.services import LoginBlockUtil


def reset_user_password(instance: Any, request: Any) -> None:
    """重置目标用户密码并站内信通知（密码策略由序列化器统一判定）。"""
    from identity.serializers.user import ResetPasswordSerializer

    serializer = ResetPasswordSerializer(instance, data=request.data, context={"request": request})
    serializer.is_valid(raise_exception=True)
    serializer.save()
    SiteMessageUtil.notify_success(
        users=instance,
        title=_("Password reset successful"),
        message=_("Your password has been reset by an administrator"),
    )


def reset_user_mfa(instance: Any) -> None:
    """重置目标用户 MFA：清空 OTP、恢复码与二次确认状态，并解除登录封锁。"""
    instance.otp_secret_key = ""
    instance.mfa_level = UserInfo.MFALevelChoices.DISABLED
    instance.save(update_fields=["otp_secret_key", "mfa_level"])
    clear_recovery_codes(instance)
    UserConfirmStateCache(instance).clear()
    LoginBlockUtil.unblock_user(instance.username)


def impersonate_user(request: Any, target: Any) -> tuple[bool, Any, Any]:
    """以目标用户身份签发模拟 token。

    :return: ``(是否成功, 响应文案, 登录载荷)``；失败时载荷为 None，文案即拒绝原因。
    """
    if target.pk == request.user.pk:
        return False, _("Impersonating yourself is not allowed"), None
    if target.is_superuser:
        return False, _("Impersonating a super administrator is not allowed"), None
    if not target.is_active:
        return False, _("Disabled users cannot be impersonated"), None
    data = start_impersonation(request, target, request.user)
    return True, _("Now impersonating user {}").format(target.username), data


def bind_im_identity(user: Any, request: Any) -> tuple[Any, Any, Any]:
    """管理员代录 IM 身份：创建或更新绑定并落审计。

    :return: ``(响应数据, 成功文案, 错误文案)``；失败时前两项为 None。
    """
    provider = (request.data.get("provider") or "").strip()
    subject = (request.data.get("subject") or "").strip()
    if not provider or not subject:
        return None, None, _("Provider and subject are required")
    conflict = UserOAuthBinding.objects.filter(provider=provider, subject=subject).exclude(user=user).first()
    if conflict:
        return None, None, _("This identity is already bound to another user")
    nickname = (request.data.get("nickname") or "").strip()
    binding, created = UserOAuthBinding.objects.update_or_create(
        user=user,
        provider=provider,
        defaults={"subject": subject, "profile": {"nickname": nickname, "source": "admin_manual"}},
    )
    OperationLog.objects.create(
        module="IM:binding",
        object_pk=str(user.pk),
        path=request.path,
        changes=json.dumps({"provider": provider, "subject": subject, "created": created}, ensure_ascii=False)[:4096],
    )
    return (
        {"pk": str(binding.pk), "created": created},
        _("IM identity bound") if created else _("IM identity updated"),
        None,
    )


def unbind_im_identity(user: Any, request: Any) -> tuple[Any, Any]:
    """管理员解绑 IM 身份（防自锁：不留任何登录方式时拒绝）。

    :return: ``(成功文案, 错误文案)``；失败时成功文案为 None。
    """
    provider = (request.data.get("provider") or "").strip()
    binding = user.oauth_bindings.filter(provider=provider).first()
    if binding is None:
        return None, _("Binding not found")
    if not UserOAuthBinding.user_has_other_login_method(user, exclude_pk=binding.pk):
        return None, _("User would lose the last login method")
    binding.delete()
    OperationLog.objects.create(
        module="IM:binding",
        object_pk=str(user.pk),
        path=request.path,
        changes=json.dumps({"provider": provider, "action": "unbind"}, ensure_ascii=False)[:4096],
    )
    return _("IM identity unbound"), None


def invite_user(instance: Any, request: Any) -> tuple[bool, Any]:
    """发送/重发邀请激活邮件：邮件通道未配置时返回可读原因，不抛异常。

    :return: ``(是否已发送, 失败文案)``。
    """
    from identity.utils import user_invite

    if not user_invite.mail_channel_configured():
        return False, user_invite.INVITE_MAIL_UNAVAILABLE_MESSAGE
    user_invite.send_invite(instance, request=request)
    return True, _("Invitation sent")


def unblock_user(instance: Any) -> None:
    """解除登录封锁（连续失败计数与锁定窗口）。"""
    LoginBlockUtil.unblock_user(instance.username)


def logout_user(instance: Any, channel_names: Any = None, operator: Any = None) -> Any:
    """强制用户全部会话下线（令牌失效 + refresh 黑名单 + WS + 会话离线）。

    用户页与在线会话页共用同一实现；``channel_names`` 仅为兼容旧调用入参，
    下线语义固定为「该用户全部会话」，避免只推 WS 而 HTTP 令牌仍可用的半失效状态。
    """
    from identity.utils.session import force_logout_user

    return force_logout_user(instance.pk, operator=operator)


def im_bindings(instance: Any) -> list[dict[str, Any]]:
    """管理端查看 IM 绑定（只读）。"""
    return list(instance.oauth_bindings.values("pk", "provider", "subject", "profile", "created_time"))
