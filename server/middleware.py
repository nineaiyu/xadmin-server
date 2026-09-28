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
from django.utils import translation
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


def _module_gate_detail(request) -> str:
    """模块网关 404 的文案（按请求语言翻译）。

    网关排在 `LocaleMiddleware` 之前（早退路径不经过语言协商），直接取 `_()` 会落到
    进程默认语言——英文界面会收到中文提示；这里按请求显式激活语言后再取文案，
    协商口径与 `LocaleMiddleware` 相同（Accept-Language / cookie / 默认语言）。
    """

    with translation.override(translation.get_language_from_request(request)):
        return str(_("Feature not enabled"))


class ModuleGateMiddleware:
    """功能模块裁剪的路由级拦截（见 common/core/modules.py）。

    命中「已停用模块」路由前缀的请求直接返回 404，语义等价于该功能不存在，
    避免出现「页面已隐藏、接口仍可达」的半残状态。未配置停用模块时零开销。
    响应体携带 ``module``（命中模块 id），供前端给出「模块已停用」专用提示。
    """

    def __init__(self, get_response):
        self.get_response = get_response

        from common.core.modules import disabled_route_patterns

        self.patterns = disabled_route_patterns()

    def __call__(self, request):
        if self.patterns:
            from common.core.modules import match_disabled_module

            module_id = match_disabled_module(request.path)
            if module_id:
                return JsonResponse(
                    {"code": 1001, "detail": _module_gate_detail(request), "data": None, "module": module_id},
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
