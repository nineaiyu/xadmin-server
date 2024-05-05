#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : auth
# author : ly_13
# date : 6/6/2023
import base64
import hashlib
import time

from django.conf import settings
from django.contrib import auth
from django.utils import timezone
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView, TokenObtainPairView
from user_agents import parse

from common.cache.storage import BlackAccessTokenCache
from common.core.config import SysConfig
from common.core.response import ApiResponse
from common.core.throttle import RegisterThrottle
from common.utils.request import get_request_ip, get_browser, get_os
from common.utils.token import make_token, verify_token
from system.models import UserInfo, DeptInfo, UserLoginLog
from system.utils.captcha import CaptchaAuth
from system.utils.serializer import UserLoginLogSerializer


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


def get_token_lifetime(user_obj):
    access_token_lifetime = settings.SIMPLE_JWT.get('ACCESS_TOKEN_LIFETIME')
    refresh_token_lifetime = settings.SIMPLE_JWT.get('REFRESH_TOKEN_LIFETIME')
    return {
        'access_token_lifetime': int(access_token_lifetime.total_seconds()),
        'refresh_token_lifetime': int(refresh_token_lifetime.total_seconds()),
        'username': user_obj.username
    }


def get_request_ident(request):
    http_user_agent = request.META.get('HTTP_USER_AGENT')
    http_accept = request.META.get('HTTP_ACCEPT')
    remote_addr = BaseThrottle().get_ident(request)
    return base64.b64encode(f"{http_user_agent}{http_accept}{remote_addr}".encode("utf-8")).decode('utf-8')


class TempTokenView(APIView):
    """获取临时token"""
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        token = make_token(get_request_ident(request), time_limit=600, force_new=True).encode('utf-8')
        return ApiResponse(token=token, lifetime=settings.SIMPLE_JWT.get('REFRESH_TOKEN_LIFETIME').days)


class CaptchaView(APIView):
    """获取验证码"""
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        return ApiResponse(**CaptchaAuth().generate())


class RegisterView(APIView):
    """用户注册"""
    permission_classes = []
    authentication_classes = []
    throttle_classes = [RegisterThrottle]

    def post(self, request, *args, **kwargs):
        data = request.data
        client_id = get_request_ident(request)
        token = data.get('token')
        username = data.get('username')
        password = data.get('password')
        channel = data.get('channel', 'default')
        if not SysConfig.REGISTER:
            return ApiResponse(code=1001, detail='禁止注册')

        if verify_token(token, client_id, success_once=True) and username and password:
            if UserInfo.objects.filter(username=username).count():
                return ApiResponse(code=1001, detail='用户名已经存在，请换个试试')

            user = auth.authenticate(username=username, password=password)
            update_fields = ['last_login']
            if not user:
                user = UserInfo.objects.create_user(username=username, password=password, first_name=username,
                                                    nickname=username)
                if channel and user:
                    dept = DeptInfo.objects.filter(is_active=True, auto_bind=True, code=channel).first()
                    if not dept:
                        dept = DeptInfo.objects.filter(is_active=True, auto_bind=True).first()
                    if dept:
                        user.dept = dept
                        user.dept_belong = dept
                        update_fields.extend(['dept_belong', 'dept'])

            if user.is_active:
                refresh = RefreshToken.for_user(user)
                result = {
                    'refresh': str(refresh),
                    'access': str(refresh.access_token),
                }
                user.last_login = timezone.now()
                user.save(update_fields=update_fields)
                result.update(**get_token_lifetime(user))
                request.user = user
                save_login_log(request)
                return ApiResponse(data=result)
        return ApiResponse(code=1001, detail='token校验失败,请刷新页面重试')


class LoginView(TokenObtainPairView):
    """用户登录"""

    def post(self, request, *args, **kwargs):
        if not SysConfig.LOGIN:
            return ApiResponse(code=1001, detail='禁止登录')

        client_id = get_request_ident(request)
        token = request.data.get('token')
        captcha_key = request.data.get('captcha_key')
        captcha_code = request.data.get('captcha_code')
        if client_id and token and captcha_key and verify_token(token, client_id, success_once=True):
            is_valid = CaptchaAuth(captcha_key=captcha_key).valid(captcha_code)
            if is_valid:
                serializer = self.get_serializer(data=request.data)
                try:
                    serializer.is_valid(raise_exception=True)
                except Exception as e:
                    request.user = UserInfo.objects.filter(username=request.data.get('username')).first()
                    save_login_log(request, status=False)
                    return ApiResponse(code=9999, detail=e.args[0])
                data = serializer.validated_data
                data.update(get_token_lifetime(serializer.user))
                request.user = serializer.user
                save_login_log(request)
                return ApiResponse(data=data)
            else:
                return ApiResponse(code=9999, detail='验证码不正确，请重新输入')

        return ApiResponse(code=9999, detail='token校验失败,请刷新页面重试')


class RefreshTokenView(TokenRefreshView):
    """刷新Token"""

    def post(self, request, *args, **kwargs):
        data = super().post(request, *args, **kwargs).data
        data.update(get_token_lifetime(request.user))
        return ApiResponse(data=data)


class LogoutView(APIView):
    """用户登出"""

    def post(self, request):
        """
        登出账户，并且将账户的access 和 refresh token 加入黑名单
        """
        payload = request.auth.payload
        exp = payload.get('exp')
        user_id = payload.get('user_id')
        timeout = exp - time.time()
        BlackAccessTokenCache(user_id, hashlib.md5(request.auth.token).hexdigest()).set_storage_cache(1, timeout)
        if request.data.get('refresh'):
            try:
                token = RefreshToken(request.data.get('refresh'))
                token.blacklist()
            except Exception as e:
                pass
        return ApiResponse()
