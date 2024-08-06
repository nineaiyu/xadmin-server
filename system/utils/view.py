#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : view
# author : ly_13
# date : 8/6/2024

import base64

from django.conf import settings
from rest_framework.throttling import BaseThrottle

from common.base.utils import AESCipherV2
from common.utils.token import verify_token
from system.utils.captcha import CaptchaAuth


def get_token_lifetime(user_obj):
    access_token_lifetime = settings.SIMPLE_JWT.get('ACCESS_TOKEN_LIFETIME')
    refresh_token_lifetime = settings.SIMPLE_JWT.get('REFRESH_TOKEN_LIFETIME')
    return {
        'access_token_lifetime': int(access_token_lifetime.total_seconds()),
        'refresh_token_lifetime': int(refresh_token_lifetime.total_seconds()),
        # 'username': user_obj.username
    }


def get_request_ident(request):
    http_user_agent = request.META.get('HTTP_USER_AGENT')
    http_accept = request.META.get('HTTP_ACCEPT')
    remote_addr = BaseThrottle().get_ident(request)
    return base64.b64encode(f"{http_user_agent}{http_accept}{remote_addr}".encode("utf-8")).decode('utf-8')


def check_captcha(need, captcha_key, captcha_code):
    if not need or (captcha_key and CaptchaAuth(captcha_key=captcha_key).valid(captcha_code)):
        return True
    # raise Exception("验证码输入有误,请重新输入")


def check_tmp_token(need, token, client_id, success_once=True):
    if not need or (client_id and token and verify_token(token, client_id, success_once)):
        return True
    # raise Exception("临时Token校验失败,请刷新页面重试")


def get_username_password(need, request, token):
    username = request.data.get('username')
    password = request.data.get('password')
    if need:
        username = AESCipherV2(token).decrypt(username)
        password = AESCipherV2(token).decrypt(password)
    return username, password
