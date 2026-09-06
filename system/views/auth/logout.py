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
from mfa.cache import UserConfirmStateCache


class LogoutAPIView(GenericAPIView):
    """用户登出"""

    @extend_schema(
        request=OpenApiRequest(build_object_type(properties={'refresh': build_basic_type(OpenApiTypes.STR)})),
        responses=get_default_response_schema()
    )
    def post(self, request):
        """用户登出"""
        # 登出同时清除敏感操作二次确认状态，避免下个会话在有效期内绕过二次验证
        # （放在 auth 判断之前：session 等非 JWT 认证方式下 request.auth 为空）
        if getattr(request, 'user', None) and request.user.is_authenticated:
            UserConfirmStateCache(request.user).clear()
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
            except Exception:
                pass
        logout(request)
        # 登出同时清除敏感操作二次确认状态，避免下个会话在有效期内绕过二次验证
        UserConfirmStateCache(request.user).clear()
        return ApiResponse()
