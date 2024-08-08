#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : logout
# author : ly_13
# date : 8/8/2024
import hashlib
import time

from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from common.cache.storage import BlackAccessTokenCache
from common.core.response import ApiResponse


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

