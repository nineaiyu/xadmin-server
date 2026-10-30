#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
settings app 对外服务契约层。

其他 app 需要使用 settings 的密码策略与安全防护能力时，只允许从本模块导入，
禁止直接 import settings.utils 等内部实现，避免 app 间横向依赖扩散。
"""

from collections.abc import Callable
from typing import Any

from django.conf import settings as django_settings
from django.utils.translation import gettext_lazy as _

from settings.models import Setting
from settings.utils.password import (
    PASSWORD_EXPIRED_MESSAGE,
    check_history_password,
    check_leak_password,
    check_password_rules,
    get_password_check_rules,
    is_password_expired,
    record_password_hash,
)
from settings.utils.security import (
    LoginBlockUtil,
    LoginIpBlockUtil,
    MFABlockUtils,
    RegisterBlockUtil,
    ResetBlockUtil,
    SendVerifyCodeBlockUtil,
)

__all__ = [
    # 模型契约（启动自检等迁移就绪探测场景）
    "Setting",
    "check_password_rules",
    "check_leak_password",
    "check_history_password",
    "record_password_hash",
    "is_password_expired",
    "PASSWORD_EXPIRED_MESSAGE",
    "get_password_check_rules",
    "LoginBlockUtil",
    "LoginIpBlockUtil",
    "MFABlockUtils",
    "RegisterBlockUtil",
    "ResetBlockUtil",
    "SendVerifyCodeBlockUtil",
    "build_setting_effective",
    "email_service_ready",
    "sms_service_ready",
]


def __getattr__(name: str) -> Any:
    # 惰性再导出（视图模块导入较重，启动期不需要）
    _lazy = {
        "AiAssistantSettingSerializer": "settings.serializers.ai",
        "BaseSettingViewSet": "settings.views.settings",
    }
    if name in _lazy:
        from importlib import import_module

        value = getattr(import_module(_lazy[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ================================ 实际生效状态诊断 ================================
#
# 管理端设置页展示的开关是原始配置值，实际可用性 = 场景开关 AND 依赖通道可用性。
# 诊断在此统一合成，由各设置 tab 的 GET/PUT 响应以只读派生字段 ``effective``
# 下发（BaseSettingViewSet.get_readonly_values 注入，SettingSaveContractMixin
# 在序列化输出时挂载），前端设置页通用渲染，各 tab 无需自行实现。
#
# 口径约束：login / register / reset 三场景的 ``enabled`` 直接取自登录页能力
# 清单（identity.services.auth_capabilities，惰性 import 以避免模块级互引成环），
# 管理端诊断与登录页入口显隐永不漂移；通道判定为开关级（与能力清单同口径）。
# 投递服务就绪判定要求可实际投递：邮件 SMTP 缺 EMAIL_HOST 即未生效（locmem /
# console / dummy 测试后端视为就绪）；短信需开关 + 已收录服务商凭据齐全（阿里云）；
# LDAP 配置缺失同理为硬阻断（与 LdapConfigError 口径一致）。


def _email_service_state() -> tuple[bool, list[str]]:
    """邮件投递服务状态：开关 + SMTP 主机（测试用 locmem/console 视为可用）。"""
    if not django_settings.EMAIL_ENABLED:
        return False, []
    backend = str(getattr(django_settings, "EMAIL_BACKEND", "") or "")
    if any(marker in backend for marker in ("locmem", "console", "dummy")):
        return True, []
    if not django_settings.EMAIL_HOST:
        return False, [_("SMTP server address (EMAIL_HOST) is empty; emails may fail to be delivered")]
    return True, []


def _sms_service_state() -> tuple[bool, list[str]]:
    """短信投递服务状态：开关 + 凭据完整性（阿里云可判，缺失即未生效）。"""
    if not django_settings.SMS_ENABLED:
        return False, []
    if django_settings.SMS_BACKEND == "alibaba" and not (
        django_settings.ALIBABA_ACCESS_KEY_ID
        and django_settings.ALIBABA_ACCESS_KEY_SECRET
        and django_settings.ALIBABA_VERIFY_SIGN_NAME
        and django_settings.ALIBABA_VERIFY_TEMPLATE_CODE
    ):
        return False, [_("SMS provider credentials are incomplete; verify codes may fail to be sent")]
    return True, []


def _ldap_state() -> tuple[bool, list[str]]:
    """LDAP 登录状态:配置项缺失即不可用（与 LdapConfigError 口径一致，硬判定）。"""
    if not django_settings.LDAP_AUTH_ENABLED:
        return False, []
    reasons: list[str] = []
    if not django_settings.LDAP_SERVER_URI:
        reasons.append(_("LDAP server address (LDAP_SERVER_URI) is not configured"))
    if not django_settings.LDAP_USER_SEARCH_BASE:
        reasons.append(_("LDAP user search base (LDAP_USER_SEARCH_BASE) is not configured"))
    return not reasons, reasons


def _email_channel_reason(scenario_switch_key: str) -> str | None:
    """邮箱通道在给定场景下不可用的原因;可用返回 None（开关级口径）。"""
    if not getattr(django_settings, scenario_switch_key, False):
        return str(_("Email verify code switch is off"))
    if not django_settings.EMAIL_ENABLED:
        return str(_("Email service is disabled"))
    return None


def _sms_channel_reason(scenario_switch_key: str) -> str | None:
    """短信通道在给定场景下不可用的原因;可用返回 None（开关级口径）。"""
    if not getattr(django_settings, scenario_switch_key, False):
        return str(_("Sms verify code switch is off"))
    if not django_settings.SMS_ENABLED:
        return str(_("Sms service is disabled"))
    return None


def _login_capabilities() -> dict[str, Any]:
    """登录页能力清单（惰性 import:identity 侧大量 import settings.services,
    模块级互引会成环;函数级惰性 import 是跨 app 门禁的官方逃生门）。"""
    from identity.services.auth_capabilities import build_login_capabilities

    return build_login_capabilities()


def _login_effective_items() -> list[dict[str, Any]]:
    cap = _login_capabilities()
    basic_enabled = bool(cap["basic"]["enabled"])
    code_enabled = bool(cap["code"]["enabled"])
    # reasons 只在未生效时给出:可用时列其他关闭通道的原因是噪音(tab 内开关可见)
    code_reasons: list[str] = []
    if not code_enabled:
        code_reasons = [
            reason
            for reason in (
                _email_channel_reason("SECURITY_LOGIN_BY_EMAIL_ENABLED"),
                _sms_channel_reason("SECURITY_LOGIN_BY_SMS_ENABLED"),
            )
            if reason
        ]
        code_reasons.append(_("Verify code login requires at least one available channel (email or sms)"))
    return [
        {
            "title": _("Basic login"),
            "enabled": basic_enabled,
            "reasons": [] if basic_enabled else [_("Basic login switch is off")],
            "notices": [],
        },
        {
            "title": _("Verify code login"),
            "enabled": code_enabled,
            "reasons": code_reasons,
            "notices": [],
        },
    ]


def _scenario_effective_items(
    title: str,
    enabled: bool,
    access_enabled: bool,
    access_off_reason: str,
    channel_switch_email: str,
    channel_switch_sms: str,
    no_channel_reason: str,
) -> list[dict[str, Any]]:
    """注册/重置类场景:单一入口项,可用性 = 场景开关 AND 任一验证码通道可用。

    reasons 只在未生效时给出(可用时列其他关闭通道的原因是噪音)。
    """
    reasons: list[str] = []
    if not enabled:
        if not access_enabled:
            reasons.append(access_off_reason)
        else:
            reasons += [
                reason
                for reason in (
                    _email_channel_reason(channel_switch_email),
                    _sms_channel_reason(channel_switch_sms),
                )
                if reason
            ]
            reasons.append(no_channel_reason)
    return [{"title": title, "enabled": enabled, "reasons": reasons, "notices": []}]


def _register_effective_items() -> list[dict[str, Any]]:
    cap = _login_capabilities()
    return _scenario_effective_items(
        _("Register"),
        bool(cap["register"]["enabled"]),
        django_settings.SECURITY_REGISTER_ACCESS_ENABLED,
        _("Register switch is off"),
        "SECURITY_REGISTER_BY_EMAIL_ENABLED",
        "SECURITY_REGISTER_BY_SMS_ENABLED",
        _("Register requires at least one available channel (email or sms)"),
    )


def _reset_effective_items() -> list[dict[str, Any]]:
    cap = _login_capabilities()
    return _scenario_effective_items(
        _("Reset password"),
        bool(cap["reset"]["enabled"]),
        django_settings.SECURITY_RESET_PASSWORD_ACCESS_ENABLED,
        _("Reset password switch is off"),
        "SECURITY_RESET_PASSWORD_BY_EMAIL_ENABLED",
        "SECURITY_RESET_PASSWORD_BY_SMS_ENABLED",
        _("Reset password requires at least one available channel (email or sms)"),
    )


def _bind_email_effective_items() -> list[dict[str, Any]]:
    ready, service_reasons = _email_service_state()
    enabled = django_settings.SECURITY_BIND_EMAIL_ACCESS_ENABLED and ready
    reasons: list[str] = []
    if not django_settings.SECURITY_BIND_EMAIL_ACCESS_ENABLED:
        reasons.append(_("Bind email switch is off"))
    if not ready:
        reasons.extend(service_reasons or [_("Email service is disabled")])
    return [{"title": _("Bind email"), "enabled": enabled, "reasons": reasons, "notices": []}]


def _bind_phone_effective_items() -> list[dict[str, Any]]:
    ready, service_reasons = _sms_service_state()
    enabled = django_settings.SECURITY_BIND_PHONE_ACCESS_ENABLED and ready
    reasons: list[str] = []
    if not django_settings.SECURITY_BIND_PHONE_ACCESS_ENABLED:
        reasons.append(_("Bind phone switch is off"))
    if not ready:
        reasons.extend(service_reasons or [_("Sms service is disabled")])
    return [{"title": _("Bind phone"), "enabled": enabled, "reasons": reasons, "notices": []}]


def _email_effective_items() -> list[dict[str, Any]]:
    enabled, reasons = _email_service_state()
    if not enabled and not reasons:
        reasons = [_("Email service is disabled")]
    return [{"title": _("Email service"), "enabled": enabled, "reasons": reasons, "notices": []}]


def _sms_effective_items() -> list[dict[str, Any]]:
    enabled, reasons = _sms_service_state()
    if not enabled and not reasons:
        reasons = [_("Sms service is disabled")]
    return [{"title": _("Sms service"), "enabled": enabled, "reasons": reasons, "notices": []}]


def _ldap_effective_items() -> list[dict[str, Any]]:
    enabled, reasons = _ldap_state()
    return [{"title": _("LDAP login"), "enabled": enabled, "reasons": reasons, "notices": []}]


_EFFECTIVE_BUILDERS: dict[str, Callable[[], list[dict[str, Any]]]] = {
    "security_login_auth": _login_effective_items,
    "security_register_auth": _register_effective_items,
    "security_reset_password_auth": _reset_effective_items,
    "security_bind_email_auth": _bind_email_effective_items,
    "security_bind_phone_auth": _bind_phone_effective_items,
    "email": _email_effective_items,
    "sms": _sms_effective_items,
    "ldap": _ldap_effective_items,
}


def build_setting_effective(category: str) -> dict[str, Any] | None:
    """构建设置 tab 的「实际生效状态」诊断载荷;未登记的 tab 返回 None（不下发）。

    形状:``{"items": [{"title", "enabled", "reasons", "notices"}]}``——
    reasons 为未生效原因；notices 为兼容软提示的保留字段（当前投递服务
    均按可实际投递硬判定，恒为空）。
    """
    builder = _EFFECTIVE_BUILDERS.get(category)
    if builder is None:
        return None
    return {"items": builder()}


def email_service_ready() -> bool:
    """邮件通道是否可实际投递（测试后端视为已就绪）。"""
    return _email_service_state()[0]


def sms_service_ready() -> bool:
    """短信通道是否可实际投递（开关 + 已收录服务商凭据完整性）。"""
    return _sms_service_state()[0]
