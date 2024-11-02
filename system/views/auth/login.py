#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : login
# author : ly_13
# date : 8/8/2024

from django.conf import settings
from django.contrib.auth import authenticate
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_object_type, build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.exceptions import APIException
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from common.base.utils import AESCipherV2
from common.core.response import ApiResponse
from common.core.throttle import LoginThrottle
from common.swagger.utils import get_default_response_schema
from common.utils.request import get_request_ip
from settings.utils.security import LoginBlockUtil, LoginIpBlockUtil
from system.models import UserInfo, UserLoginLog
from system.utils.auth import get_username_password, get_token_lifetime, check_is_block, check_token_and_captcha, \
    save_login_log, verify_sms_email_code, check_different_city_login_if_need


def login_failed(request, username):
    ipaddr = get_request_ip(request)
    login_block_util = LoginBlockUtil(username, ipaddr)
    login_ip_block = LoginIpBlockUtil(ipaddr)
    request.user = UserInfo.objects.filter(username=username).first()
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
    raise APIException(detail)


def login_success(request, user_obj, login_type=UserLoginLog.LoginTypeChoices.USERNAME):
    ipaddr = get_request_ip(request)
    login_block_util = LoginBlockUtil(user_obj.username, ipaddr)
    login_ip_block = LoginIpBlockUtil(ipaddr)
    login_block_util.clean_failed_count()
    login_ip_block.clean_block_if_need()
    request.user = user_obj
    check_different_city_login_if_need(user_obj, ipaddr)
    save_login_log(request, login_type=login_type)


class BasicLoginAPIView(TokenObtainPairView):
    """用户登录"""
    throttle_classes = [LoginThrottle]

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    'username': build_basic_type(OpenApiTypes.STR),
                    'password': build_basic_type(OpenApiTypes.STR),
                    'token': build_basic_type(OpenApiTypes.STR),
                    'captcha_key': build_basic_type(OpenApiTypes.STR),
                    'captcha_code': build_basic_type(OpenApiTypes.STR),
                },
                required=['username', 'password'],
            )
        ),
        responses=get_default_response_schema(
            {
                'data': build_object_type(
                    properties={
                        'refresh': build_basic_type(OpenApiTypes.STR),
                        'access': build_basic_type(OpenApiTypes.STR),
                        'access_token_lifetime': build_basic_type(OpenApiTypes.NUMBER),
                        'refresh_token_lifetime': build_basic_type(OpenApiTypes.NUMBER)
                    }
                )
            }
        )
    )
    def post(self, request, *args, **kwargs):
        """用户名密码登录"""
        if not settings.SECURITY_LOGIN_ACCESS_ENABLED:
            return ApiResponse(code=1001, detail=_("Login forbidden"))

        ipaddr = get_request_ip(request)
        client_id, token = check_token_and_captcha(request, settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED,
                                                   settings.SECURITY_LOGIN_CAPTCHA_ENABLED)

        username, password = get_username_password(settings.SECURITY_LOGIN_ENCRYPTED_ENABLED, request, token)

        check_is_block(username, ipaddr)

        serializer = self.get_serializer(data={'username': username, 'password': password})
        try:
            serializer.is_valid(raise_exception=True)
        except Exception as e:
            return login_failed(request, username)
        data = serializer.validated_data
        data.update(get_token_lifetime(serializer.user))
        login_success(request, serializer.user)
        return ApiResponse(data=data)

    @extend_schema(
        responses=get_default_response_schema(
            {
                'data': build_object_type(
                    properties={
                        'access': build_basic_type(OpenApiTypes.BOOL),
                        'captcha': build_basic_type(OpenApiTypes.BOOL),
                        'token': build_basic_type(OpenApiTypes.BOOL),
                        'encrypted': build_basic_type(OpenApiTypes.BOOL),
                        'lifetime': build_basic_type(OpenApiTypes.NUMBER),
                        'reset': build_basic_type(OpenApiTypes.BOOL),
                        'basic': build_basic_type(OpenApiTypes.BOOL),
                    }
                )
            }
        )
    )
    def get(self, request, *args, **kwargs):
        """获取登录配置信息"""
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


class VerifyCodeLoginAPIView(TokenObtainPairView):
    """用户验证码登录"""
    throttle_classes = [LoginThrottle]

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    'password': build_basic_type(OpenApiTypes.STR),
                    'verify_token': build_basic_type(OpenApiTypes.STR),
                    'verify_code': build_basic_type(OpenApiTypes.STR),
                },
                required=['verify_token', 'verify_code'],
            )
        ),
        responses=get_default_response_schema(
            {
                'data': build_object_type(
                    properties={
                        'refresh': build_basic_type(OpenApiTypes.STR),
                        'access': build_basic_type(OpenApiTypes.STR),
                        'access_token_lifetime': build_basic_type(OpenApiTypes.NUMBER),
                        'refresh_token_lifetime': build_basic_type(OpenApiTypes.NUMBER)
                    }
                )
            }
        )
    )
    def post(self, request, *args, **kwargs):
        """验证码登录"""
        if not settings.SECURITY_LOGIN_ACCESS_ENABLED:
            return ApiResponse(code=1001, detail=_("Login forbidden"))
        ipaddr = get_request_ip(request)
        query_key, target, verify_token = verify_sms_email_code(request, LoginBlockUtil)
        check_is_block(target, ipaddr)

        if query_key == 'username':
            password = request.data.get('password')
            if settings.SECURITY_LOGIN_ENCRYPTED_ENABLED:
                password = AESCipherV2(verify_token).decrypt(password)
            user = authenticate(**{query_key: target}, password=password)
            if not user:
                login_failed(request, target)
        else:
            user = UserInfo.objects.get(**{query_key: target})

        refresh = RefreshToken.for_user(user)
        result = {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }
        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])
        result.update(**get_token_lifetime(user))
        login_success(request, user, login_type=UserLoginLog.get_login_type(query_key))
        return ApiResponse(data=result)
