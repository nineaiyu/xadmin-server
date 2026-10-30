#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : auth_captcha
"""登录前置校验：人机 ticket / 临时令牌 / 凭据解密，以及统一的校验异常。

``ValidateError`` 统一 HTTP 400（登录类失败不应触发前端 401 刷新页面）；
``get_username_password`` 按 ``SECURITY_LOGIN_ENCRYPTED_ENABLED`` 解密账密。
"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import APIException

from captcha import disposal
from captcha import signals as captcha_signals
from captcha.const import current_risk_mode
from captcha.contracts import InteractiveCaptcha, captcha_globally_enabled
from common.base.crypto import AESCipherV2
from common.utils.request import get_request_ident, get_request_ip
from common.utils.token import verify_token_cache


class ValidateError(APIException):
    """校验类失败统一返回 HTTP 400，避免登录失败触发前端 401 处理导致页面刷新"""

    status_code = 400


def check_captcha(
    need: Any,
    captcha_ticket: Any = None,
    scene: str = "login",
    request: Any = None,
    action: str = "",
) -> Any:
    """校验一次性人机 ticket（scene + action + sid + fp 强绑定）。"""
    if not need or not captcha_globally_enabled():
        return True
    ip = get_request_ip(request) if request is not None else ""
    headers = getattr(request, "headers", {}) if request is not None else {}
    fp = str(headers.get("X-Captcha-Fp") or "")
    sid = str(getattr(request, "COOKIES", {}).get("captcha_sid") or "") if request is not None else ""
    if captcha_ticket and InteractiveCaptcha().consume_ticket(
        str(captcha_ticket), scene, ip=ip, action=action, fp=fp, sid=sid
    ):
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


DECRYPT_FAILED_MESSAGE = _("The submitted credential is invalid, please refresh the page and try again")


def decrypt_submitted(value: Any, key: Any) -> str:
    """受控解密前端 AES 提交值：坏 base64/短密文/错钥/空明文统一 400。

    匿名登录/注册/重置/发码端点都可能收到任意密文；直接调用
    ``AESCipherV2.decrypt`` 会让 ``binascii.Error`` / ``IndexError`` 穿透成 500。
    """
    try:
        plain = AESCipherV2(key).decrypt(value)
    except Exception as exc:  # noqa: BLE001 任意解密异常统一受控拒绝
        raise ValidateError(DECRYPT_FAILED_MESSAGE) from exc
    if not plain:
        raise ValidateError(DECRYPT_FAILED_MESSAGE)
    return plain


def get_username_password(need: Any, request: Any, token: Any) -> Any:
    username = request.data.get("username")
    password = request.data.get("password")
    if need:
        username = decrypt_submitted(username, token)
        password = decrypt_submitted(password, token)
    return username, password
