# -*- coding: utf-8 -*-
"""server/middleware.py 单元测试。

覆盖：
1. SQLCountMiddleware 仅在 DEBUG 下启用，启用时输出 X-SQL-COUNT；
2. StartMiddleware / EndMiddleware 仅在 DEBUG_DEV 下启用；
3. RequestMiddleware 生成/透传 X-Request-Id 并设置 thread-local request；
4. RefererCheckMiddleware 的放行与拦截分支。
"""
import pytest
from django.core.exceptions import MiddlewareNotUsed
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from server.middleware import EndMiddleware, RefererCheckMiddleware, RequestMiddleware, SQLCountMiddleware, \
    StartMiddleware
from server.utils import get_current_request

pytestmark = pytest.mark.django_db

rf = RequestFactory()


def _response():
    return HttpResponse("ok")


class TestSQLCountMiddleware:
    def test_disabled_without_debug(self):
        with pytest.raises(MiddlewareNotUsed):
            SQLCountMiddleware(lambda r: _response())

    @override_settings(DEBUG=True)
    def test_enabled_with_debug_sets_header(self):
        middleware = SQLCountMiddleware(lambda r: _response())
        response = middleware(rf.get("/api/system/user"))
        assert response["X-SQL-COUNT"] == "-2"  # 空查询列表 len-2


class TestStartEndMiddleware:
    def test_start_disabled_without_debug_dev(self):
        with pytest.raises(MiddlewareNotUsed):
            StartMiddleware(lambda r: _response())

    def test_end_disabled_without_debug_dev(self):
        with pytest.raises(MiddlewareNotUsed):
            EndMiddleware(lambda r: _response())

    @override_settings(DEBUG_DEV=True)
    def test_start_sets_time_attributes(self):
        start = StartMiddleware(lambda r: _response())
        request = rf.get("/api/system/user")
        start(request)
        assert hasattr(request, "_s_time_start")
        assert hasattr(request, "_s_time_end")

    @override_settings(DEBUG_DEV=True)
    def test_end_sets_time_attributes(self):
        end = EndMiddleware(lambda r: _response())
        request = rf.get("/api/system/user")
        end(request)
        assert hasattr(request, "_e_time_start")
        assert hasattr(request, "_e_time_end")

    @override_settings(DEBUG_DEV=True)
    def test_health_path_returns_timing_info(self):
        class JsonResponse(HttpResponse):
            data = {"code": 1000}

        def handler(request):
            request._e_time_start = 1.0
            request._e_time_end = 2.0
            return JsonResponse('{"code": 1000}', content_type="application/json")

        chain = StartMiddleware(EndMiddleware(handler))
        request = rf.get("/api/common/api/health")
        response = chain(request)
        body = response.content.decode()
        # health 探活返回三段耗时，便于定位慢在中间件还是视图
        assert "pre_middleware_time" in body
        assert "api_time" in body
        assert "post_middleware_time" in body


class TestRequestMiddleware:
    def test_generates_request_uuid(self):
        middleware = RequestMiddleware(lambda r: _response())
        request = rf.get("/api/system/user")
        response = middleware(request)
        assert str(request.request_uuid) == response["X-Request-Id"]
        assert get_current_request() is request

    def test_reuses_upstream_request_id(self):
        middleware = RequestMiddleware(lambda r: _response())
        request = rf.get("/api/system/user", HTTP_X_REQUEST_ID="gw-abc-123")
        response = middleware(request)
        assert request.request_uuid == "gw-abc-123"
        assert response["X-Request-Id"] == "gw-abc-123"

    def test_sanitizes_upstream_request_id(self):
        """上游 ID 中的非法字符被剔除，且超长截断到 64 位"""
        middleware = RequestMiddleware(lambda r: _response())
        request = rf.get("/", HTTP_X_REQUEST_ID="abc<script>!" + "x" * 100)
        middleware(request)
        assert request.request_uuid == ("abcscript" + "x" * 100)[:64]
        assert len(request.request_uuid) == 64

    def test_empty_upstream_id_generates_new(self):
        middleware = RequestMiddleware(lambda r: _response())
        request = rf.get("/", HTTP_X_REQUEST_ID="")
        middleware(request)
        assert request.request_uuid


class TestRefererCheckMiddleware:
    def test_disabled_by_default(self):
        with pytest.raises(MiddlewareNotUsed):
            RefererCheckMiddleware(lambda r: _response())

    @override_settings(REFERER_CHECK_ENABLED=True)
    def test_allows_request_without_referer(self):
        middleware = RefererCheckMiddleware(lambda r: _response())
        assert middleware(rf.get("/")).status_code == 200

    @override_settings(REFERER_CHECK_ENABLED=True)
    def test_allows_same_host_referer(self):
        middleware = RefererCheckMiddleware(lambda r: _response())
        request = rf.get("/", HTTP_REFERER="https://testserver/login", HTTP_HOST="testserver")
        assert middleware(request).status_code == 200

    @override_settings(REFERER_CHECK_ENABLED=True)
    def test_allows_same_host_referer_with_path_prefix(self):
        """http(s):// 前缀被剥掉后再比较，其余路径不受影响"""
        middleware = RefererCheckMiddleware(lambda r: _response())
        request = rf.get("/", HTTP_REFERER="http://testserver/", HTTP_HOST="testserver")
        assert middleware(request).status_code == 200

    @override_settings(REFERER_CHECK_ENABLED=True)
    def test_rejects_foreign_referer(self):
        middleware = RefererCheckMiddleware(lambda r: _response())
        request = rf.get("/", HTTP_REFERER="https://evil.example.com/x", HTTP_HOST="testserver")
        response = middleware(request)
        assert response.status_code == 403
        assert "CSRF" in response.content.decode()
