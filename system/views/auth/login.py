#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : login
# author : ly_13
# date : 8/8/2024

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from common.core.response import ApiResponse
from common.core.throttle import LoginThrottle
from common.utils.request import get_request_ip
from settings.utils.security import LoginBlockUtil, LoginIpBlockUtil
from system.models import UserInfo
from system.utils.auth import get_username_password, get_token_lifetime, check_is_block, check_token_and_captcha, \
    save_login_log, verify_sms_email_code


class BasicLoginView(TokenObtainPairView):
    """用户登录"""
    throttle_classes = [LoginThrottle]

    def post(self, request, *args, **kwargs):
        if not settings.SECURITY_LOGIN_ACCESS_ENABLED:
            return ApiResponse(code=1001, detail=_("Login forbidden"))

        ipaddr = get_request_ip(request)
        client_id, token = check_token_and_captcha(request, settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED,
                                                   settings.SECURITY_LOGIN_CAPTCHA_ENABLED)

        username, password = get_username_password(settings.SECURITY_LOGIN_ENCRYPTED_ENABLED, request, token)

        check_is_block(username, ipaddr)

        login_block_util = LoginBlockUtil(username, ipaddr)
        login_ip_block = LoginIpBlockUtil(ipaddr)

        serializer = self.get_serializer(data={'username': username, 'password': password})
        try:
            serializer.is_valid(raise_exception=True)
        except Exception as e:
            request.user = UserInfo.objects.filter(username=request.data.get('username')).first()
            save_login_log(request, status=False)
            login_block_util.incr_failed_count()
            login_ip_block.set_block_if_need()

            times_remainder = login_block_util.get_remainder_times()
            if times_remainder > 0:
                detail = _(
                    "The username or password you entered is incorrect, "
                    "please enter it again. "
                    "You can also try {times_try} times "
                    "(The account will be temporarily locked for {block_time} minutes)"
                ).format(times_try=times_remainder, block_time=settings.SECURITY_LOGIN_LIMIT_TIME)
            else:
                detail = _("The account has been locked (please contact admin to unlock it or try"
                           " again after {} minutes)").format(settings.SECURITY_LOGIN_LIMIT_TIME)
            return ApiResponse(code=9999, detail=detail)
        data = serializer.validated_data
        data.update(get_token_lifetime(serializer.user))
        request.user = serializer.user
        save_login_log(request)

        login_block_util.clean_failed_count()
        login_ip_block.clean_block_if_need()

        return ApiResponse(data=data)

    def get(self, request, *args, **kwargs):
        config = {
            'access': settings.SECURITY_LOGIN_ACCESS_ENABLED,
            'captcha': settings.SECURITY_LOGIN_CAPTCHA_ENABLED,
            'token': settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED,
            'encrypted': settings.SECURITY_LOGIN_ENCRYPTED_ENABLED,
            'lifetime': settings.SIMPLE_JWT.get('REFRESH_TOKEN_LIFETIME').days,
            'reset': settings.SECURITY_RESET_PASSWORD_ACCESS_ENABLED,
            'basic': settings.SECURITY_LOGIN_BY_BASIC_ENABLED,
        }
        return ApiResponse(data=config)


class VerifyCodeLoginView(TokenObtainPairView):
    """用户登录"""
    throttle_classes = [LoginThrottle]

    def post(self, request, *args, **kwargs):
        if not settings.SECURITY_LOGIN_ACCESS_ENABLED:
            return ApiResponse(code=1001, detail=_("Login forbidden"))

        query_key, target, verify_token = verify_sms_email_code(request, LoginBlockUtil)

        user = UserInfo.objects.get(**{query_key: target})

        refresh = RefreshToken.for_user(user)
        result = {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }
        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])
        result.update(**get_token_lifetime(user))
        request.user = user
        save_login_log(request)
        return ApiResponse(data=result)
