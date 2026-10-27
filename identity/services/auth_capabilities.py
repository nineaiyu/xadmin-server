#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""登录页能力清单服务:各登录方式可用性的唯一计算口径。

「登录页要渲染哪些登录方式」的判定收口在此(单一事实源):
前端登录页外壳经 ``GET /api/identity/auth/capabilities`` 一次性消费,
入口按钮显隐、页面骨架判断不再由前端写死或从分散端点拼凑。

与 ``auth/verify?category=`` 的分工:本服务只回答「方式级可用性 + 账密登录
细节开关」;验证码场景的页内细节(发码冷却、密码规则等)仍由 verify 端点承载。
"""

from typing import Any

from django.conf import settings

from captcha.contracts import build_captcha_meta, captcha_globally_enabled
from identity.utils.oauth import get_providers


def _channel(scenario_enabled: bool, backend_ready: bool) -> bool:
    """场景开关 AND 通道可实际投递（与设置页「实际生效状态」同一硬判定）。

    仅看 ``EMAIL_ENABLED`` / ``SMS_ENABLED`` 会把「开关开着但发不出」的通道
    也展示成可用入口；就绪判定收口在 ``settings.services``（SMTP 缺主机、
    短信凭据不全即未就绪）。
    """
    return scenario_enabled and backend_ready


def _email_ready() -> bool:
    from settings.services import email_service_ready

    return email_service_ready()


def _sms_ready() -> bool:
    from settings.services import sms_service_ready

    return sms_service_ready()


def _oauth_entries() -> list[dict[str, str]]:
    """已启用 provider 的登录页投影(不含密钥;与 providers 端点同口径)。"""
    return [
        {"key": item["key"], "name": item["name"], "flavor": item.get("flavor", "oauth2")}
        for item in get_providers(enabled_only=True)
    ]


def build_login_capabilities() -> dict[str, Any]:
    """汇总各登录方式的能力清单(开关计算集中于此,便于组合测试)。"""
    email_ready = _email_ready()
    sms_ready = _sms_ready()
    login_email = _channel(settings.SECURITY_LOGIN_BY_EMAIL_ENABLED, email_ready)
    login_sms = _channel(settings.SECURITY_LOGIN_BY_SMS_ENABLED, sms_ready)
    register_email = _channel(settings.SECURITY_REGISTER_BY_EMAIL_ENABLED, email_ready)
    register_sms = _channel(settings.SECURITY_REGISTER_BY_SMS_ENABLED, sms_ready)
    reset_email = _channel(settings.SECURITY_RESET_PASSWORD_BY_EMAIL_ENABLED, email_ready)
    reset_sms = _channel(settings.SECURITY_RESET_PASSWORD_BY_SMS_ENABLED, sms_ready)
    # 注册/重置流程必须经短信或邮箱验证码完成,通道全关时流程走不通,入口即隐藏
    captcha_meta = build_captcha_meta("login")
    return {
        "access": settings.SECURITY_LOGIN_ACCESS_ENABLED,
        "lifetime": settings.SIMPLE_JWT.get("REFRESH_TOKEN_LIFETIME").days,
        "basic": {
            "enabled": settings.SECURITY_LOGIN_BY_BASIC_ENABLED,
            "captcha": captcha_globally_enabled() and settings.SECURITY_LOGIN_CAPTCHA_ENABLED,
            "captcha_types": captcha_meta["types"],
            "captcha_image_fallback": captcha_meta["image_fallback"],
            "captcha_flow": captcha_meta.get("captcha_flow", "always"),
            "token": settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED,
            "encrypted": settings.SECURITY_LOGIN_ENCRYPTED_ENABLED,
        },
        "code": {"enabled": login_email or login_sms, "email": login_email, "sms": login_sms},
        "register": {
            "enabled": settings.SECURITY_REGISTER_ACCESS_ENABLED and (register_email or register_sms),
            "email": register_email,
            "sms": register_sms,
        },
        "reset": {
            "enabled": settings.SECURITY_RESET_PASSWORD_ACCESS_ENABLED and (reset_email or reset_sms),
            "email": reset_email,
            "sms": reset_sms,
        },
        "oauth": _oauth_entries(),
    }
