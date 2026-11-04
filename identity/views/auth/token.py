#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : token
# author : ly_13
# date : 8/10/2024
from typing import Any

from django.conf import settings
from drf_spectacular.plumbing import build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.throttling import AnonRateThrottle
from rest_framework_simplejwt.views import TokenRefreshView

from common.core.response import ApiResponse
from common.core.throttle import TempTokenThrottle
from common.swagger.utils import get_default_response_schema
from common.utils.request import get_request_ident
from common.utils.token import make_token_cache
from identity.services.auth_cookie import refresh_token_from_cookie, session_token_response
from identity.utils.auth import get_token_lifetime


class TempTokenAPIView(GenericAPIView):
    """临时Token

    匿名可达；每次调用强制生成新缓存令牌（Redis 写放大面），在全局匿名
    限流（IP 维度）之上叠加更严的专用档。
    """

    permission_classes: list[type] = []
    authentication_classes: list[type] = []
    throttle_classes = [AnonRateThrottle, TempTokenThrottle]

    @extend_schema(responses=get_default_response_schema({"token": build_basic_type(OpenApiTypes.STR)}))
    def get(self, request: Any) -> Any:
        """获取临时令牌"""
        time_limit = int(getattr(settings, "SECURITY_TEMP_TOKEN_EXPIRE", 600) or 600)
        token = make_token_cache(get_request_ident(request), time_limit=time_limit, force_new=True).encode("utf-8")
        return ApiResponse(token=token)


class RefreshTokenAPIView(TokenRefreshView):
    """刷新Token

    匿名可达（simplejwt TokenViewBase 自带空认证/权限链，过期 access 不会阻断
    刷新）；refresh 凭证支持**双读**：请求体优先，缺失时回落 ``X-Refresh-Token``
    Cookie（HttpOnly Cookie 模式下前端读不到 token，只能走 Cookie）。Cookie 凭证
    是浏览器自动携带的环境凭证，回落时按登录态 API 同一口径执行 Origin/Referer
    的 CSRF 兜底（跨站源拒绝），body 凭证不受影响。
    """

    def _merge_cookie_refresh(self, request: Any) -> None:
        """body 无 refresh 且 Cookie 有值时并入请求体（Cookie 通道先过 CSRF）。"""
        if request.data.get("refresh"):
            return
        cookie_refresh = refresh_token_from_cookie(request)
        if not cookie_refresh:
            return
        from common.core.auth.authentication import enforce_cookie_csrf

        enforce_cookie_csrf(request)
        request._full_data = {**request.data, "refresh": cookie_refresh}

    def post(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        self._merge_cookie_refresh(request)
        data = super().post(request, *args, **kwargs).data
        data.update(get_token_lifetime(request.user))
        # 轮换开关开启时响应含新 refresh：Cookie 模式必须随响应重下两条 Cookie，
        # 否则旧 refresh 已被拉黑、新 refresh 只在 body（Cookie 会话拿不到）即死锁
        return session_token_response(request, data)
