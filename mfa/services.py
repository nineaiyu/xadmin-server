#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : services
"""mfa app 对外服务契约层。

其他 app 需要使用敏感操作二次验证 / 登录 MFA 能力时，只允许从本模块导入。
核心用法见 docs/architecture/mfa.md。
"""

from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from common.utils.request import get_request_ip
from common.utils.verify_code import TokenTempCache
from mfa.backends import get_backend, get_enabled_backends, get_user_mfa_policy
from mfa.cache import UserConfirmStateCache
from mfa.const import CONFIRM_TYPE_LEVEL, ConfirmType
from settings.services import MFABlockUtils

logger = get_logger(__name__)


def _get_request_ip(request: Any) -> str:
    return get_request_ip(request) if request else ""


def _serialize_backend(backend: Any) -> dict[str, Any]:
    return {
        "name": backend.name,
        "display_name": str(backend.display_name),
        "placeholder": str(backend.placeholder),
        "challenge_required": backend.challenge_required,
    }


def is_method_binding_allowed(user: Any, method: str) -> bool:
    """绑定入口与验证同口径：方式是否在账号方式白名单（交集）内。

    白名单收窄到空集的账号（如共享演示账号）禁止绑定任何 MFA，防止绑定后触发
    策略强制二次验证锁死共享登录。供绑定入口（含跨 app 的 system Passkey 注册）
    使用——业务层只 import 本模块，不直连 mfa.backends。
    """
    methods = get_user_mfa_policy(user).get("methods")
    return methods is None or method in methods


def _check_mfa_block(user: Any, ipaddr: str) -> Any:
    """MFA 验证防爆破锁定校验，返回锁定提示文案（未锁定返回 None）"""
    if MFABlockUtils(user.username, ipaddr).is_block():
        return _(
            "Too many failures, the account has been locked "
            "(please contact admin to unlock it or try again after {} minutes)"
        ).format(settings.SECURITY_LOGIN_LIMIT_TIME)
    return None


def get_confirm_methods(user: Any, request: Any = None, confirm_type: str = ConfirmType.MFA) -> list[dict[str, Any]]:
    """获取用户在指定验证类型下可用的验证方式（低级别请求允许使用高级别方式）"""
    if confirm_type == ConfirmType.PASSWORD:
        levels = [ConfirmType.MFA, ConfirmType.PASSWORD]
    else:
        levels = [ConfirmType.MFA]
    return [_serialize_backend(b) for b in get_enabled_backends(user, request=request, levels=levels)]


def check_user_mfa_code(user: Any, method: str, code: str, request: Any = None) -> tuple[bool, Any]:
    """校验动态验证码（含防爆破锁定），返回 (是否通过, 失败原因)"""
    ipaddr = _get_request_ip(request)
    locked = _check_mfa_block(user, ipaddr)
    if locked:
        return False, str(locked)

    backend = get_backend(user, method, request=request)
    if not backend:
        return False, _("The verification method is unavailable")

    ok, msg = backend.check_code(code)
    block = MFABlockUtils(user.username, ipaddr)
    if ok:
        block.clean_failed_count()
        return True, ""
    block.incr_failed_count()
    return False, msg


def verify_user_confirm(
    user: Any, method: str, code: str, request: Any = None, confirm_type: str = ConfirmType.MFA
) -> tuple[bool, Any]:
    """校验验证码并写入二次确认状态（有效期内敏感操作免重复验证）"""
    backend = get_backend(user, method, request=request)
    if not backend:
        return False, _("The verification method is unavailable")
    if CONFIRM_TYPE_LEVEL[backend.confirm_level] < CONFIRM_TYPE_LEVEL[confirm_type]:
        return False, _("The verification method does not meet the security requirements")

    ok, msg = check_user_mfa_code(user, method, code, request=request)
    if ok:
        UserConfirmStateCache(user).set(backend.confirm_level, method)
    return ok, msg


def send_user_mfa_code(user: Any, method: str, request: Any = None) -> tuple[bool, Any]:
    """下发挑战验证码（短信/邮件），返回 (是否成功, 失败原因)"""
    backend = get_backend(user, method, request=request)
    if not backend:
        return False, _("The verification method is unavailable")
    if not backend.challenge_required:
        return False, _("This method does not need a verification code to be sent")
    result: tuple[bool, Any] = backend.send_challenge()
    return result


def is_login_mfa_required(user: Any) -> bool:
    """登录 MFA 判定：
    - 个人开启（mfa_enabled）→ 必须验证。这是用户自身的安全配置，不受全局开关影响；
    - 角色级强制（`UserRole.mfa_required`）→ 有可用验证方式即必须验证
      （无可用方式时降级放行 + 告警，避免登录死锁）；
    - 全局「登录 MFA 强制」开启 → 已绑定 OTP 的账号一律验证（含个人已关闭的）；
    - 未绑定密钥无法验证，不拦截。
    """
    if user.mfa_enabled:
        return True
    if get_user_mfa_policy(user).get("mfa_required"):
        if get_login_mfa_methods(user):
            return True
        logger.warning("Role requires MFA but no method available, skip. user: %s", user.username)
        return False
    if not user.otp_secret_key:
        return False
    return bool(settings.SECURITY_MFA_LOGIN_PROTECT_ENABLED)


def generate_login_mfa_token(user: Any) -> str:
    """生成登录 MFA 临时令牌（不含任何真实凭证，一次性使用）"""
    token: str = TokenTempCache.generate_cache_token(
        settings.SECURITY_MFA_LOGIN_TOKEN_TTL, {"user_id": user.pk, "scene": "login_mfa"}
    )
    return token


def validate_login_mfa_token(token: str) -> Any:
    """校验登录 MFA 临时令牌，返回对应用户（无效或已禁用返回 None）"""
    data = TokenTempCache.validate_cache_token(token)
    if not data or data.get("scene") != "login_mfa":
        return None
    return get_user_model().objects.filter(pk=data.get("user_id"), is_active=True).first()


def get_login_mfa_methods(user: Any, request: Any = None) -> list[dict[str, Any]]:
    """获取登录 MFA 可用的验证方式（密码方式在登录场景无意义，不参与）"""
    return get_confirm_methods(user, request=request, confirm_type=ConfirmType.MFA)


def clear_recovery_codes(user: Any) -> None:
    """作废用户全部 OTP 恢复码（供解绑 / 管理员重置 MFA 的链路同步调用）"""
    from mfa import recovery

    recovery.clear_codes(user)


def ensure_user_confirmed(request: Any, confirm_type: str = ConfirmType.MFA) -> None:
    """敏感操作二次确认校验（412 协议）——供其他 app 的 ViewSet/action 手动校验。

    未通过时抛 HTTP 412（type=user_confirm_required），前端拦截弹验证窗并自动重发；
    验证通过后的确认状态写入缓存（JWT 无 session），有效期内免重复验证。
    """
    from mfa.confirm import ensure_user_confirmed as _ensure

    return _ensure(request, confirm_type)
