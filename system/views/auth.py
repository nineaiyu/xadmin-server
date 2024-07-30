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
from django.utils.translation import gettext_lazy as _
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView, TokenObtainPairView
from user_agents import parse

from common.base.utils import AESCipherV2
from common.cache.storage import BlackAccessTokenCache
from common.core.config import SysConfig
from common.core.response import ApiResponse
from common.core.throttle import RegisterThrottle, LoginThrottle
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


class TempTokenView(APIView):
    """获取临时token"""
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        token = make_token(get_request_ident(request), time_limit=600, force_new=True).encode('utf-8')
        return ApiResponse(token=token)


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
        if not SysConfig.REGISTER:
            return ApiResponse(code=1001, detail=_("Registration forbidden"))

        client_id = get_request_ident(request)
        token = request.data.get('token')
        captcha_key = request.data.get('captcha_key')
        captcha_code = request.data.get('captcha_code')

        if not check_tmp_token(SysConfig.NEED_REGISTER_TOKEN, token, client_id):
            return ApiResponse(code=9999, detail=_("Temporary Token validation failed. Please try again"))
        if not check_captcha(SysConfig.NEED_REGISTER_CAPTCHA, captcha_key, captcha_code):
            return ApiResponse(code=9999, detail=_("Captcha validation failed. Please try again"))
        channel = request.data.get('channel', 'default')
        username, password = get_username_password(SysConfig.NEED_REGISTER_ENCRYPTED, request, token)
        if UserInfo.objects.filter(username=username).count():
            return ApiResponse(code=1001, detail=_("The username already exists, please try another one"))

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

    def get(self, request, *args, **kwargs):
        config = {
            'access': SysConfig.REGISTER,
            'captcha': SysConfig.NEED_REGISTER_CAPTCHA,
            'token': SysConfig.NEED_REGISTER_TOKEN,
            'encrypted': SysConfig.NEED_LOGIN_ENCRYPTED,
        }
        return ApiResponse(data=config)

class LoginView(TokenObtainPairView):
    """用户登录"""
    throttle_classes = [LoginThrottle]

    def post(self, request, *args, **kwargs):
        if not SysConfig.LOGIN:
            return ApiResponse(code=1001, detail=_("Login forbidden"))

        client_id = get_request_ident(request)
        token = request.data.get('token')
        captcha_key = request.data.get('captcha_key')
        captcha_code = request.data.get('captcha_code')

        if not check_tmp_token(SysConfig.NEED_LOGIN_TOKEN, token, client_id):
            return ApiResponse(code=9999, detail=_("Temporary Token validation failed. Please try again"))
        if not check_captcha(SysConfig.NEED_LOGIN_CAPTCHA, captcha_key, captcha_code):
            return ApiResponse(code=9999, detail=_("Captcha validation failed. Please try again"))

        username, password = get_username_password(SysConfig.NEED_LOGIN_ENCRYPTED, request, token)
        serializer = self.get_serializer(data={'username': username, 'password': password})
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

    def get(self, request, *args, **kwargs):
        config = {
            'access': SysConfig.LOGIN,
            'captcha': SysConfig.NEED_LOGIN_CAPTCHA,
            'token': SysConfig.NEED_LOGIN_TOKEN,
            'encrypted': SysConfig.NEED_LOGIN_ENCRYPTED,
            'lifetime': settings.SIMPLE_JWT.get('REFRESH_TOKEN_LIFETIME').days
        }
        return ApiResponse(data=config)

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
