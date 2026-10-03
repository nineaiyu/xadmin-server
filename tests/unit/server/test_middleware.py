# -*- coding: utf-8 -*-
"""server/middleware.py 单元测试。

覆盖：
1. SQLCountMiddleware 仅在 DEBUG 下启用，启用时输出 X-SQL-COUNT；
2. StartMiddleware / EndMiddleware 仅在 DEBUG_DEV 下启用；
3. RequestMiddleware 生成/透传 X-Request-Id 并设置 thread-local request；
4. RefererCheckMiddleware 的放行与拦截分支；
5. ：双模中间件 async 链（__acall__）与 sync 链行为等价
   current_request 经 contextvars + sync_to_async 在同步视图线程可读。
"""

import asyncio
import re

import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import MiddlewareNotUsed
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from server.middleware import (
    EndMiddleware,
    ModuleGateMiddleware,
    RefererCheckMiddleware,
    RequestMiddleware,
    SQLCountMiddleware,
    StartMiddleware,
)
from server.utils import get_current_request

pytestmark = pytest.mark.django_db

rf = RequestFactory()


def _response():
    return HttpResponse("ok")


async def _async_response(request):
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
    def test_rejects_lookalike_host_suffix(self):
        """对抗性：站点名相同但域名不同（testserver.evil.com）必须拒绝。"""
        middleware = RefererCheckMiddleware(lambda r: _response())
        request = rf.get("/", HTTP_REFERER="https://testserver.evil.com/login", HTTP_HOST="testserver")
        assert middleware(request).status_code == 403

    @override_settings(REFERER_CHECK_ENABLED=True)
    def test_allows_bare_host_referer(self):
        """裸 host（无路径）仍放行：锚定不能收紧到只认 `host/`。"""
        middleware = RefererCheckMiddleware(lambda r: _response())
        request = rf.get("/", HTTP_REFERER="https://testserver", HTTP_HOST="testserver")
        assert middleware(request).status_code == 200

    @override_settings(REFERER_CHECK_ENABLED=True)
    def test_rejects_foreign_referer(self):
        middleware = RefererCheckMiddleware(lambda r: _response())
        request = rf.get("/", HTTP_REFERER="https://evil.example.com/x", HTTP_HOST="testserver")
        response = middleware(request)
        assert response.status_code == 403
        assert "CSRF" in response.content.decode()


class TestAsyncMiddlewareChain:
    """双模中间件在 async 链上的行为与 sync 链等价。"""

    def test_request_middleware_acall_equivalence(self):
        middleware = RequestMiddleware(_async_response)
        request = rf.get("/api/system/user", HTTP_X_REQUEST_ID="gw-abc-123")
        response = asyncio.run(middleware(request))
        assert request.request_uuid == "gw-abc-123"
        assert response["X-Request-Id"] == "gw-abc-123"

    def test_acall_request_visible_in_sync_view_thread(self):
        """contextvars 传播实证：__acall__ 写入的 current_request
        在同步视图线程（经 sync_to_async 的 context 复制）可读。"""

        async def handler(request):
            def _view():
                assert get_current_request() is request
                return _response()

            return await sync_to_async(_view, thread_sensitive=True)()

        middleware = RequestMiddleware(handler)
        request = rf.get("/api/system/user")
        asyncio.run(middleware(request))

    def test_acall_generates_uuid_when_upstream_empty(self):
        middleware = RequestMiddleware(_async_response)
        request = rf.get("/")
        response = asyncio.run(middleware(request))
        assert request.request_uuid
        assert response["X-Request-Id"] == str(request.request_uuid)

    def test_module_gate_acall_returns_404(self, monkeypatch):
        from common.core import modules as modules_mod

        monkeypatch.setattr(
            modules_mod, "match_disabled_module", lambda path: "chat" if path.startswith("/api/chat") else None
        )
        middleware = ModuleGateMiddleware(_async_response)
        middleware.patterns = [re.compile("^/api/chat")]
        response = asyncio.run(middleware(rf.get("/api/chat/messages")))
        assert response.status_code == 404

        request = rf.get("/api/system/user")
        assert asyncio.run(middleware(request)).status_code == 200

    def test_module_gate_sync_chain_unchanged(self, monkeypatch):
        """sync 链（WSGI / 测试 client）行为不回归。"""
        from common.core import modules as modules_mod

        monkeypatch.setattr(modules_mod, "match_disabled_module", lambda path: "chat")
        middleware = ModuleGateMiddleware(lambda r: _response())
        middleware.patterns = [re.compile("^/api/chat")]
        assert middleware(rf.get("/api/chat/messages")).status_code == 404

    @override_settings(REFERER_CHECK_ENABLED=True)
    def test_referer_check_acall_rejects_foreign(self):
        middleware = RefererCheckMiddleware(_async_response)
        request = rf.get("/", HTTP_REFERER="https://evil.example.com/x", HTTP_HOST="testserver")
        response = asyncio.run(middleware(request))
        assert response.status_code == 403

    @override_settings(REFERER_CHECK_ENABLED=True)
    def test_referer_check_acall_allows_same_host(self):
        middleware = RefererCheckMiddleware(_async_response)
        request = rf.get("/", HTTP_REFERER="https://testserver/login", HTTP_HOST="testserver")
        assert asyncio.run(middleware(request)).status_code == 200
