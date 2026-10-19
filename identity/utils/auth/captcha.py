#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : auth_captcha
"""登录前置校验：图形验证码 / 临时令牌 / 凭据解密，以及统一的校验异常。

``ValidateError`` 统一 HTTP 400（登录类失败不应触发前端 401 刷新页面）；
``get_username_password`` 按 ``SECURITY_LOGIN_ENCRYPTED_ENABLED`` 解密账密。
"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import APIException

from captcha.services import CaptchaAuth
from common.base.utils import AESCipherV2
from common.utils.request import get_request_ident
from common.utils.token import verify_token_cache


class ValidateError(APIException):
    """校验类失败统一返回 HTTP 400，避免登录失败触发前端 401 处理导致页面刷新"""

    status_code = 400


def check_captcha(need: Any, captcha_key: Any, captcha_code: Any) -> Any:
    if not need or (captcha_key and CaptchaAuth(captcha_key=captcha_key).valid(captcha_code)):
        return True
    raise ValidateError(_("Captcha validation failed. Please try again"))


def check_tmp_token(need: Any, token: Any, client_id: Any, success_once: Any = True) -> Any:
    if not need or (client_id and token and verify_token_cache(token, client_id, success_once)):
        return True
    raise ValidateError(_("Temporary Token validation failed. Please try again"))


def check_token_and_captcha(request: Any, token_enable: Any, captcha_enable: Any, success_once: Any = True) -> Any:
    client_id = get_request_ident(request)
    token = request.data.get("token")
    captcha_key = request.data.get("captcha_key")
    captcha_code = request.data.get("captcha_code")

    check_tmp_token(token_enable, token, client_id, success_once)
    check_captcha(captcha_enable, captcha_key, captcha_code)
    return client_id, token


def get_username_password(need: Any, request: Any, token: Any) -> Any:
    username = request.data.get("username")
    password = request.data.get("password")
    if need:
        username = AESCipherV2(token).decrypt(username)
        password = AESCipherV2(token).decrypt(password)
    return username, password
