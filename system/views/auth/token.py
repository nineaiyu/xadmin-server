#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : token
# author : ly_13
# date : 8/10/2024
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView

from captcha.utils import CaptchaAuth
from common.core.response import ApiResponse
from common.utils.request import get_request_ident
from common.utils.token import make_token_cache
from system.utils.auth import get_token_lifetime


class TempTokenView(APIView):
    """获取临时token"""
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        token = make_token_cache(get_request_ident(request), time_limit=600, force_new=True).encode('utf-8')
        return ApiResponse(token=token)


class CaptchaView(APIView):
    """获取验证码"""
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        return ApiResponse(**CaptchaAuth().generate())


class RefreshTokenView(TokenRefreshView):
    """刷新Token"""

    def post(self, request, *args, **kwargs):
        data = super().post(request, *args, **kwargs).data
        data.update(get_token_lifetime(request.user))
        return ApiResponse(data=data)
