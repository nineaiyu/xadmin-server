#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : views
# author : ly_13
# date : 8/12/2024

from django.conf import settings
from django.contrib.auth import login, logout
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.clickjacking import xframe_options_exempt
from drf_spectacular.utils import extend_schema
from drf_spectacular.views import (
    SpectacularJSONAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
    SpectacularYAMLAPIView,
)
from rest_framework.generics import GenericAPIView
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle
from rest_framework_simplejwt.serializers import TokenObtainSerializer

from common.base.magic import cache_response
from common.core.response import ApiResponse
from common.utils.request import get_request_ip
from settings.services import LoginBlockUtil, LoginIpBlockUtil

# 文档站登录默认回跳（next 缺失或校验不通过时使用）
DOCS_DEFAULT_NEXT = "/api-docs/swagger/"


def _safe_next_url(request) -> str:
    """登录回跳的同源校验：仅放行本站 host（request.get_host + ALLOWED_HOSTS）。

    ALLOWED_HOSTS 里的通配 "*" 不并入——那等于放行任意外部域，失去防护意义。
    """
    target = str(request.query_params.get("next") or "").strip() or DOCS_DEFAULT_NEXT
    allowed_hosts = {request.get_host()}
    allowed_hosts.update(host for host in settings.ALLOWED_HOSTS if host and host != "*")
    if url_has_allowed_host_and_scheme(target, allowed_hosts=allowed_hosts, require_https=request.is_secure()):
        return target
    return DOCS_DEFAULT_NEXT


class ApiLogin(GenericAPIView):
    """接口文档的登录接口。

    与主登录链路同口径接入账号锁定与 IP 锁定（共用同一失败计数：失败累计、
    成功清零），另加独立更严限流（api_docs_login），避免文档站成为绕过
    主登录锁定/验证码的爆破旁路。
    """

    permission_classes = ()
    serializer_class = TokenObtainSerializer
    throttle_classes = [AnonRateThrottle, ScopedRateThrottle]
    throttle_scope = "api_docs_login"

    @extend_schema(exclude=True)
    @xframe_options_exempt
    def post(self, request, *args, **kwargs):
        username = str(request.data.get("username") or "").strip()
        ipaddr = get_request_ip(request)
        login_block = LoginBlockUtil(username, ipaddr)
        ip_block = LoginIpBlockUtil(ipaddr)

        if ip_block.is_block() or login_block.is_block():
            return ApiResponse(
                code=1001,
                detail=_(
                    "The account has been locked (please contact admin to unlock it or try again after {} minutes)"
                ).format(settings.SECURITY_LOGIN_LIMIT_TIME),
            )

        try:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            login(request, serializer.user)
        except Exception:
            login_block.incr_failed_count()
            ip_block.set_block_if_need()
            return ApiResponse(code=1001, detail=_("Incorrect username/password"))

        login_block.clean_failed_count()
        ip_block.clean_block_if_need()
        return redirect(_safe_next_url(request))

    @extend_schema(exclude=True)
    @xframe_options_exempt
    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(to="/api-docs/swagger/")
        return ApiResponse(detail=_("Please enter your account information to log in"))


class ApiLogout(GenericAPIView):
    permission_classes: list[type] = []

    @extend_schema(exclude=True)
    @xframe_options_exempt
    def get(self, request, *args, **kwargs):
        logout(request)
        return redirect("/api-docs/login/")


class SchemaMixin:
    @xframe_options_exempt
    @cache_response(timeout=60 * 5, key_func="get_cache_key")
    def get(self, *args, **kwargs):
        return super().get(*args, **kwargs)  # type: ignore[misc]  # 宿主视图提供基类实现（mixin 模式）

    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f"{view_instance.__class__.__name__}_{view_method.__name__}"
        return f"{func_name}_{request.user.pk}"


@extend_schema(exclude=True)
class JsonApi(SchemaMixin, SpectacularJSONAPIView):
    pass


@extend_schema(exclude=True)
class YamlApi(SchemaMixin, SpectacularYAMLAPIView):
    pass


@extend_schema(exclude=True)
class SwaggerUI(SchemaMixin, SpectacularSwaggerView):
    pass


@extend_schema(exclude=True)
class Redoc(SchemaMixin, SpectacularRedocView):
    pass
