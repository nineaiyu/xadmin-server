#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : logout
# author : ly_13
# date : 8/8/2024
import hashlib
import time

from django.contrib.auth import logout
from drf_spectacular.plumbing import build_object_type, build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.generics import GenericAPIView
from rest_framework_simplejwt.tokens import RefreshToken

from common.cache.storage import BlackAccessTokenCache
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema


class LogoutAPIView(GenericAPIView):
    """用户登出"""

    @extend_schema(
        request=OpenApiRequest(build_object_type(properties={'refresh': build_basic_type(OpenApiTypes.STR)})),
        responses=get_default_response_schema()
    )
    def post(self, request):
        """用户登出"""
        auth = request.auth
        if not auth:
            return ApiResponse()
        exp = auth.payload.get('exp')
        user_id = auth.payload.get('user_id')
        timeout = exp - time.time()
        BlackAccessTokenCache(user_id, hashlib.md5(auth.token).hexdigest()).set_storage_cache(1, timeout)
        if request.data.get('refresh'):
            try:
                token = RefreshToken(request.data.get('refresh'))
                token.blacklist()  # 登出账户，并且将账户的access 和 refresh token 加入黑名单
            except Exception as e:
                pass
        logout(request)
        return ApiResponse()
