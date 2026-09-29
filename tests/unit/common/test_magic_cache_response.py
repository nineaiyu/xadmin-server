# -*- coding: utf-8 -*-
"""MagicCacheResponse 单飞与回源语义：
1. 命中直接回放渲染结果（视图方法不再执行）；
2. **并发未命中只回源一次**（单飞锁 + 锁内二次检查），其余请求复用首次结果；
3. 等锁超时（LockError）退化为直接回源，不把缓存争用变成 500；
4. no_cache 旁路：不读缓存也不回写；
5. 4xx/5xx 响应不回写缓存。
"""

import threading
import time

import pytest
from django.core.cache import cache
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from common.base.magic import MagicCacheResponse

pytestmark = pytest.mark.django_db


class FakeView:
    """最小视图替身：渲染链所需方法 + 调用计数（装饰器写入类体，与真实视图同形）。"""

    def __init__(self, status=200, delay=0.0):
        self.calls = 0
        self.status = status
        self.delay = delay
        self._lock = threading.Lock()

    def get_renderer_context(self):
        return {"view": self}

    def finalize_response(self, request, response, *args, **kwargs):
        response.accepted_renderer = JSONRenderer()
        response.accepted_media_type = "application/json"
        response.renderer_context = self.get_renderer_context()
        return response

    @MagicCacheResponse(timeout=60, key_func=lambda view_instance=None, **_: f"unit-{id(view_instance)}")
    def list(self, request, *args, **kwargs):
        with self._lock:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return Response({"payload": "fresh"}, status=self.status)


class _Request:
    """带查询参数的请求替身（no_cache 旁路用）。"""

    def __init__(self, **query):
        self.query_params = {key: str(value) for key, value in query.items()}


def test_second_call_serves_from_cache():
    view = FakeView()
    first = view.list(None)
    second = view.list(None)
    assert view.calls == 1
    assert first.status_code == second.status_code == 200
    assert first.content == second.content


def test_concurrent_misses_hit_view_once():
    """单飞：6 个并发请求同时未命中，视图只执行一次。"""
    view = FakeView(delay=0.15)
    barrier = threading.Barrier(6)
    results = []
    errors = []

    def worker():
        try:
            barrier.wait(timeout=5)
            results.append(view.list(None).status_code)
        except Exception as exc:  # noqa: BLE001 测试内收集断言
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert not errors, errors
    assert results == [200] * 6
    assert view.calls == 1, f"单飞失效：视图被回源 {view.calls} 次"


def test_lock_error_falls_back_to_direct_render(monkeypatch):
    """等锁超时：仍返回正常响应（不把缓存争用升级为 500），且结果照常回写。"""
    from redis.exceptions import LockError

    view = FakeView()

    class _FailingLock:
        def __enter__(self):
            raise LockError("timeout")

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(cache, "lock", lambda *args, **kwargs: _FailingLock())
    assert view.list(None).status_code == 200
    assert view.calls == 1

    monkeypatch.undo()  # 退化路径同样回写：后续请求直接命中
    assert view.list(None).status_code == 200
    assert view.calls == 1


def test_no_cache_bypass_skips_read_and_write():
    view = FakeView()
    assert view.list(_Request(no_cache=1)).status_code == 200
    # 旁路请求不回写：第二次（无 no_cache）仍需真实回源
    assert view.list(None).status_code == 200
    assert view.calls == 2


def test_error_response_not_cached():
    view = FakeView(status=500)
    assert view.list(None).status_code == 500
    assert view.list(None).status_code == 500
    assert view.calls == 2


def test_cached_response_keeps_headers():
    """命中回放要带回头部（缓存载荷存的是 headers 映射）。"""

    class _HeaderView(FakeView):
        @MagicCacheResponse(timeout=60, key_func=lambda view_instance=None, **_: f"hdr-{id(view_instance)}")
        def list(self, request, *args, **kwargs):
            response = Response({"payload": "fresh"}, status=self.status)
            response["X-Cache-Test"] = "on"
            with self._lock:
                self.calls += 1
            return response

    header_view = _HeaderView()
    assert header_view.list(None)["X-Cache-Test"] == "on"
    assert header_view.list(None)["X-Cache-Test"] == "on"
    assert header_view.calls == 1
