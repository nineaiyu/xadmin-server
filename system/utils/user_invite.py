#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : user_invite
"""邀请开户（F-11）：邀请令牌 + 邮件 + 激活设置密码。

流程：

1. 管理端 ``POST /api/system/user/{pk}/invite``（权限点 ``invite:SystemUser``）→
   :func:`send_invite`：置 ``invite_status=pending`` + 密码置为不可用（登录被拒）+
   生成一次性令牌（``TokenTempCache``，``scene=user_invite``，TTL 72 小时）+ 发邀请邮件；
2. 激活页 ``POST /api/system/auth/invite/accept``（匿名）：校验令牌 → 密码强度 / 泄露库 /
   历史校验（与注册、忘记密码重置同口径）→ ``set_password`` + ``invite_status=accepted``
   + ``record_password_hash``（顺带清除强制改密标记、刷新密码过期计时）；
3. 令牌一次性（激活即失效）；无效 / 过期 / 已激活返回可读文案，不泄露具体原因。

安全与边界：

- 未配置邮件渠道时 :func:`mail_channel_configured` 返回 False，调用方 fail-closed 返回
  可读错误，避免「以为发了邀请实际没发」；
- **重发邀请会把已激活账号重新置为待激活**（密码立即失效），前端需二次确认；
- 邀请账号采用「不可用密码」而非 ``is_active=False``：登录被拒的同时不与「停用」语义混淆
  （安全巡检 / 到期停用任务的 `is_active` 口径不受影响）。
"""

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.core.config import SysConfig
from common.utils import get_logger

logger = get_logger(__name__)

INVITE_SCENE = "user_invite"
INVITE_TOKEN_TTL = 72 * 3600  # 72 小时
INVITE_INVALID_MESSAGE = _("Invitation link is invalid or has expired")
INVITE_ACCEPTED_MESSAGE = _("Account already activated, please sign in directly")
INVITE_MAIL_UNAVAILABLE_MESSAGE = _("Mail channel is not configured, unable to send invitation")


def mail_channel_configured() -> bool:
    """邮件渠道是否可用：locmem/console 后端视为可用（开发 / 测试态）。"""
    backend = str(getattr(settings, "EMAIL_BACKEND", "") or "")
    if "locmem" in backend or "console" in backend:
        return True
    return bool(getattr(settings, "EMAIL_HOST", ""))


def invite_requested(data) -> bool:
    """请求体中的「创建即邀请」开关（F-11）：JSON 布尔与表单字符串均兼容。"""
    if data is None or not hasattr(data, "get"):
        return False
    value = data.get("invite")
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def generate_invite_token(user) -> str:
    from common.utils.verify_code import TokenTempCache

    return TokenTempCache.generate_cache_token(INVITE_TOKEN_TTL, {"scene": INVITE_SCENE, "user_id": user.pk})


def resolve_invite_token(token):
    """校验邀请令牌：返回 ``(user, state)``，state ∈ ``invalid / accepted / pending``。"""
    from common.utils.verify_code import TokenTempCache

    data = TokenTempCache.validate_cache_token(token) if token else None
    if not isinstance(data, dict) or data.get("scene") != INVITE_SCENE:
        return None, "invalid"
    from system.models import UserInfo

    user = UserInfo.objects.filter(pk=data.get("user_id")).first()
    if user is None:
        return None, "invalid"
    if user.invite_status != UserInfo.InviteStatusChoices.PENDING:
        TokenTempCache.expired_cache_token(token)
        return user, "accepted"
    return user, "pending"


def invite_link(token: str, request=None) -> str:
    base = str(getattr(SysConfig, "WEB_SITE_URL", "") or "").strip()
    if not base and request is not None:
        base = request.build_absolute_uri("/")
    if not base:
        base = "http://127.0.0.1:8848/"
    return f"{base.rstrip('/')}/#/invite/accept?token={token}"


def send_invite(user, request=None) -> str:
    """发送（或重发）邀请：置待激活 + 密码不可用 + 邮件链接；返回一次性令牌。"""
    from system.models import UserInfo

    token = generate_invite_token(user)
    user.invite_status = UserInfo.InviteStatusChoices.PENDING
    user.invited_time = timezone.now()
    user.set_unusable_password()
    user.save(update_fields=["invite_status", "invited_time", "password"])

    title = _("Account activation invitation")
    message = _(
        "You are invited to activate your account (%(username)s). "
        "Please open the link within 72 hours to set your password: %(link)s"
    ) % {"username": user.username, "link": invite_link(token, request)}
    email = getattr(user, "email", "")
    if email:
        try:
            from common.tasks import send_mail_async

            send_mail_async.delay(str(title), str(message), [email], html_message=str(message))
        except Exception:
            logger.warning("send invite mail failed. user: %s", user, exc_info=True)
    else:
        logger.warning("invite user %s has no email, invitation link is not delivered", user.username)
    logger.info("invite user %s (pending)", user.username)
    return token


def accept_invite(user, password: str):
    """设置密码并完成激活；返回 ``(ok, detail)``。"""
    from settings.utils.password import (
        check_history_password,
        check_leak_password,
        check_password_rules,
        record_password_hash,
    )
    from system.models import UserInfo

    if not check_password_rules(password, user.is_superuser):
        return False, str(_("Password does not match security rules"))
    if check_leak_password(password):
        return False, str(_("Password has been leaked, please change to another one"))
    if check_history_password(user, password):
        return False, str(
            _("Password cannot reuse the recent %(count)s passwords")
            % {"count": settings.SECURITY_PASSWORD_HISTORY_COUNT}
        )
    user.set_password(password)
    user.invite_status = UserInfo.InviteStatusChoices.ACCEPTED
    user.save(update_fields=["password", "invite_status"])
    record_password_hash(user, user.password)
    logger.info("activate invited user %s", user.username)
    return True, str(_("Activated successfully, please sign in"))
