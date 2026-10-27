#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : auth_captcha
"""登录前置校验：图形验证码 / 临时令牌 / 凭据解密，以及统一的校验异常。

``ValidateError`` 统一 HTTP 400（登录类失败不应触发前端 401 刷新页面）；
``get_username_password`` 按 ``SECURITY_LOGIN_ENCRYPTED_ENABLED`` 解密账密。
"""

from typing import Any

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import APIException

from captcha import disposal
from captcha import signals as captcha_signals
from captcha.const import CAPTCHA_MODE_INTERACTIVE, current_captcha_mode, current_risk_mode
from captcha.contracts import CaptchaAuth, InteractiveCaptcha, captcha_globally_enabled
from common.base.crypto import AESCipherV2
from common.utils.request import get_request_ident, get_request_ip
from common.utils.token import verify_token_cache


class ValidateError(APIException):
    """校验类失败统一返回 HTTP 400，避免登录失败触发前端 401 处理导致页面刷新"""

    status_code = 400


def check_captcha(
    need: Any,
    captcha_key: Any,
    captcha_code: Any,
    captcha_ticket: Any = None,
    scene: str = "login",
    request: Any = None,
    action: str = "",
) -> Any:
    """按当前模式校验人机凭据：图片码（key+code）或交互式 ticket，二选一。

    ``action`` 为服务端声明的业务动作常量（captcha.const 单源），票据按
    (scene, action) 绑定，消灭跨动作票据挪用；空值 = 兼容旧语义（只绑 scene）。
    """
    if not need or not captcha_globally_enabled():
        return True
    if current_captcha_mode() == CAPTCHA_MODE_INTERACTIVE:
        ip = get_request_ip(request) if request is not None else ""
        fp = str(getattr(request, "headers", {}).get("X-Captcha-Fp") or "") if request is not None else ""
        if captcha_ticket and InteractiveCaptcha().consume_ticket(
            str(captcha_ticket), scene, ip=ip, action=action, fp=fp
        ):
            return True
        # 无障碍回退：显式开启后图片码仍可通过（默认关闭，避免 OCR 绕过交互式题型）
        if (
            getattr(settings, "CAPTCHA_IMAGE_FALLBACK_ENABLED", False)
            and captcha_key
            and CaptchaAuth(captcha_key=captcha_key).valid(captcha_code)
        ):
            return True
    elif captcha_key and CaptchaAuth(captcha_key=captcha_key).valid(captcha_code):
        return True
    raise ValidateError(_("Captcha validation failed. Please try again"))


def require_human(request: Any, scene: str, action: str, need: Any = True, account: str = "") -> Any:
    """统一人机校验入口：冷却判定 → 凭据消费（check_captcha 薄封装）。

    新消费点应优先调用本函数；``check_captcha`` / ``check_token_and_captcha``
    保留为二开兼容的薄封装。
    """
    if not need or not captcha_globally_enabled():
        return True
    ip = get_request_ip(request) if request is not None else ""
    if current_risk_mode() == "enforce":
        dims = disposal.dimension_keys(
            ip_hash=captcha_signals.hash_ip(ip), action=action, account=captcha_signals.hash_value(account)
        )
        if disposal.cooldown_remaining(dims):
            raise ValidateError(_("Captcha validation failed. Please try again"))
    data = getattr(request, "data", {}) if request is not None else {}
    return check_captcha(
        True,
        data.get("captcha_key"),
        data.get("captcha_code"),
        data.get("captcha_ticket"),
        scene=scene,
        request=request,
        action=action,
    )


def check_tmp_token(need: Any, token: Any, client_id: Any, success_once: Any = True) -> Any:
    if not need or (client_id and token and verify_token_cache(token, client_id, success_once)):
        return True
    raise ValidateError(_("Temporary Token validation failed. Please try again"))


def check_token_and_captcha(
    request: Any,
    token_enable: Any,
    captcha_enable: Any,
    success_once: Any = True,
    scene: str = "login",
    action: str = "",
) -> Any:
    client_id = get_request_ident(request)
    token = request.data.get("token")

    check_tmp_token(token_enable, token, client_id, success_once)
    require_human(request, scene, action, need=captcha_enable)
    return client_id, token


def get_username_password(need: Any, request: Any, token: Any) -> Any:
    username = request.data.get("username")
    password = request.data.get("password")
    if need:
        username = AESCipherV2(token).decrypt(username)
        password = AESCipherV2(token).decrypt(password)
    return username, password
