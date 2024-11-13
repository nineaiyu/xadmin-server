#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : token
# author : ly_13
# date : 8/10/2024
from drf_spectacular.plumbing import build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework_simplejwt.views import TokenRefreshView

from captcha.utils import CaptchaAuth
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils.request import get_request_ident
from common.utils.token import make_token_cache
from system.utils.auth import get_token_lifetime


class TempTokenAPIView(GenericAPIView):
    """临时Token"""
    permission_classes = []
    authentication_classes = []

    @extend_schema(responses=get_default_response_schema({'token': build_basic_type(OpenApiTypes.STR)}))
    def get(self, request):
        """获取{cls}"""
        token = make_token_cache(get_request_ident(request), time_limit=600, force_new=True).encode('utf-8')
        return ApiResponse(token=token)


class CaptchaAPIView(GenericAPIView):
    """图片验证码"""
    permission_classes = []
    authentication_classes = []

    @extend_schema(
        responses=get_default_response_schema(
            {
                'captcha_image': build_basic_type(OpenApiTypes.STR),
                'captcha_key': build_basic_type(OpenApiTypes.STR),
                'length': build_basic_type(OpenApiTypes.NUMBER)
            }
        )
    )
    def get(self, request):
        """获取{cls}"""
        return ApiResponse(**CaptchaAuth(request=request).generate())


class RefreshTokenAPIView(TokenRefreshView):
    """刷新Token"""

    def post(self, request, *args, **kwargs):
        data = super().post(request, *args, **kwargs).data
        data.update(get_token_lifetime(request.user))
        return ApiResponse(data=data)
