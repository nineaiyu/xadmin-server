#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : view
# author : ly_13
# date : 8/6/2024

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import APIException
from user_agents import parse

from captcha.utils import CaptchaAuth
from common.base.utils import AESCipherV2
from common.utils.request import get_request_ip, get_browser, get_os, get_request_ident
from common.utils.token import verify_token_cache
from common.utils.verify_code import TokenTempCache, SendAndVerifyCodeUtil
from settings.utils.security import LoginIpBlockUtil, LoginBlockUtil
from system.models import UserLoginLog
from system.serializers.log import UserLoginLogSerializer


def get_token_lifetime(user_obj):
    access_token_lifetime = settings.SIMPLE_JWT.get('ACCESS_TOKEN_LIFETIME')
    refresh_token_lifetime = settings.SIMPLE_JWT.get('REFRESH_TOKEN_LIFETIME')
    return {
        'access_token_lifetime': int(access_token_lifetime.total_seconds()),
        'refresh_token_lifetime': int(refresh_token_lifetime.total_seconds()),
        # 'username': user_obj.username
    }


def check_captcha(need, captcha_key, captcha_code):
    if not need or (captcha_key and CaptchaAuth(captcha_key=captcha_key).valid(captcha_code)):
        return True
    raise APIException(_("Captcha validation failed. Please try again"))


def check_tmp_token(need, token, client_id, success_once=True):
    if not need or (client_id and token and verify_token_cache(token, client_id, success_once)):
        return True
    raise APIException(_("Temporary Token validation failed. Please try again"))


def check_token_and_captcha(request, token_enable, captcha_enable, success_once=True):
    client_id = get_request_ident(request)
    token = request.data.get('token')
    captcha_key = request.data.get('captcha_key')
    captcha_code = request.data.get('captcha_code')

    check_tmp_token(token_enable, token, client_id, success_once)
    check_captcha(captcha_enable, captcha_key, captcha_code)
    return client_id, token


def get_username_password(need, request, token):
    username = request.data.get('username')
    password = request.data.get('password')
    if need:
        username = AESCipherV2(token).decrypt(username)
        password = AESCipherV2(token).decrypt(password)
    return username, password


def check_is_block(username, ipaddr, ip_block=LoginIpBlockUtil, login_block=LoginBlockUtil):
    if ip_block and ip_block(ipaddr).is_block():
        ip_block(ipaddr).set_block_if_need()
        raise APIException(_("The address has been locked (please contact admin to unlock it or try"
                             " again after {} minutes)").format(settings.SECURITY_LOGIN_IP_LIMIT_TIME))

    if login_block and login_block(username, ipaddr).is_block():
        raise APIException(_("The account has been locked (please contact admin to unlock it or try"
                             " again after {} minutes)").format(settings.SECURITY_LOGIN_LIMIT_TIME))


def save_login_log(request, login_type=UserLoginLog.LoginTypeChoices.USERNAME, status=True):
    data = {
        'ipaddress': get_request_ip(request),
        'browser': get_browser(request),
        'system': get_os(request),
        'status': status,
        'agent': str(parse(request.META['HTTP_USER_AGENT'])),
        'login_type': login_type
    }
    serializer = UserLoginLogSerializer(data=data, request=request, all_fields=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()


def verify_sms_email_code(request, block_utils):
    verify_token = request.data.get('verify_token')
    verify_code = request.data.get('verify_code')
    ipaddr = get_request_ip(request)
    ip_block = LoginIpBlockUtil(ipaddr)

    if not verify_token or not verify_code:
        raise APIException(_("Operation failed. Abnormal data"))

    data = TokenTempCache.validate_cache_token(verify_token)
    if not data:
        ip_block.set_block_if_need()
        raise APIException(_('Token is invalid or expired'))

    target = data.get('target')
    query_key = data.get('query_key')
    check_is_block(target, ipaddr, login_block=block_utils)
    block_util = block_utils(target, ipaddr)

    try:
        SendAndVerifyCodeUtil(target).verify(verify_code)
    except Exception as e:
        block_util.incr_failed_count()
        ip_block.set_block_if_need()

        times_remainder = block_util.get_remainder_times()
        if times_remainder > 0:
            detail = _(
                "{error} please enter it again. "
                "You can also try {times_try} times "
                "(The account will be temporarily locked for {block_time} minutes)"
            ).format(times_try=times_remainder, block_time=settings.SECURITY_LOGIN_LIMIT_TIME, error=str(e))
        else:
            detail = _("The account has been locked (please contact admin to unlock it or try"
                       " again after {} minutes)").format(settings.SECURITY_LOGIN_LIMIT_TIME)

        raise APIException(detail)

    return query_key, target, verify_token
