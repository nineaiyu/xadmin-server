#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : middleware
# author : ly_13
# date : 10/18/2024
import json
import re
import time
import uuid

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.http import HttpResponseForbidden, JsonResponse
from django.utils.translation import gettext_lazy as _

from .utils import set_current_request


class SQLCountMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        if not settings.DEBUG:
            raise MiddlewareNotUsed

    def __call__(self, request):
        from django.db import connection

        response = self.get_response(request)
        response["X-SQL-COUNT"] = len(connection.queries) - 2
        return response


class StartMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        if not settings.DEBUG_DEV:
            raise MiddlewareNotUsed

    def __call__(self, request):
        request._s_time_start = time.time()
        response = self.get_response(request)
        request._s_time_end = time.time()
        if request.path == "/api/common/api/health":
            data = response.data
            data["pre_middleware_time"] = request._e_time_start - request._s_time_start
            data["api_time"] = request._e_time_end - request._e_time_start
            data["post_middleware_time"] = request._s_time_end - request._e_time_end
            response.content = json.dumps(data)
            response.headers["Content-Length"] = str(len(response.content))
            response.headers["Content-Type"] = "application/json"
        return response


class EndMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        if not settings.DEBUG_DEV:
            raise MiddlewareNotUsed

    def __call__(self, request):
        request._e_time_start = time.time()
        response = self.get_response(request)
        request._e_time_end = time.time()
        return response


class ModuleGateMiddleware:
    """功能模块裁剪的路由级拦截（见 common/core/modules.py）。

    命中「已停用模块」路由前缀的请求直接返回 404，语义等价于该功能不存在，
    避免出现「页面已隐藏、接口仍可达」的半残状态。未配置停用模块时零开销。
    """

    def __init__(self, get_response):
        self.get_response = get_response

        from common.core.modules import disabled_route_patterns

        self.patterns = disabled_route_patterns()

    def __call__(self, request):
        if self.patterns:
            path = request.path
            for pattern in self.patterns:
                if pattern.match(path):
                    return JsonResponse(
                        {"code": 1001, "detail": str(_("Feature not enabled")), "data": None},
                        status=404,
                    )
        return self.get_response(request)


class RequestMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def get_request_uuid(request):
        # 优先沿用网关/上游传入的请求 ID，便于跨服务日志串联；无则生成新的
        upstream_id = re.sub(r"[^0-9a-zA-Z\-_]", "", request.headers.get("X-Request-Id", ""))[:64]
        return upstream_id or uuid.uuid4()

    def __call__(self, request):
        request.request_uuid = self.get_request_uuid(request)
        set_current_request(request)
        response = self.get_response(request)
        # 回写响应头，便于前端/网关按请求 ID 关联日志与反馈问题
        response["X-Request-Id"] = str(request.request_uuid)
        return response


class RefererCheckMiddleware:
    def __init__(self, get_response):
        if not settings.REFERER_CHECK_ENABLED:
            raise MiddlewareNotUsed
        self.get_response = get_response
        self.http_pattern = re.compile("https?://")

    def check_referer(self, request):
        referer = request.META.get("HTTP_REFERER", "")
        referer = self.http_pattern.sub("", referer)
        if not referer:
            return True
        remote_host = request.get_host()
        return referer.startswith(remote_host)

    def __call__(self, request):
        match = self.check_referer(request)
        if not match:
            return HttpResponseForbidden("CSRF CHECK ERROR")
        response = self.get_response(request)
        return response
