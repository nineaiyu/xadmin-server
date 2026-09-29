# -*- coding: utf-8 -*-
"""固定窗口限流通用入口（allow_by_identity / allow_by_ip）。

用于非 DRF 场景：Django 原生验证码端点（P0）与 WebSocket 消费者（聊天发送限流）。
"""

import pytest

from common.core.throttle import allow_by_identity, allow_by_ip


class TestAllowByIdentity:
    def test_window_limit(self):
        assert allow_by_identity("user-1", scope="unit_test", limit=2, window_seconds=60) is True
        assert allow_by_identity("user-1", scope="unit_test", limit=2, window_seconds=60) is True
        assert allow_by_identity("user-1", scope="unit_test", limit=2, window_seconds=60) is False

    def test_identities_are_isolated(self):
        assert allow_by_identity("user-a", scope="unit_iso", limit=1, window_seconds=60) is True
        assert allow_by_identity("user-a", scope="unit_iso", limit=1, window_seconds=60) is False
        # 另一身份独立计数（不串号）
        assert allow_by_identity("user-b", scope="unit_iso", limit=1, window_seconds=60) is True

    def test_scopes_are_isolated(self):
        assert allow_by_identity("user-1", scope="unit_scope_a", limit=1, window_seconds=60) is True
        assert allow_by_identity("user-1", scope="unit_scope_b", limit=1, window_seconds=60) is True

    def test_cache_failure_fails_open(self, monkeypatch):
        """缓存故障放行：限流是加固，不应因缓存抖动阻断业务。"""

        def _boom(*args, **kwargs):
            raise RuntimeError("redis down")

        monkeypatch.setattr("django.core.cache.cache.add", _boom)
        assert allow_by_identity("user-1", scope="unit_fail_open", limit=1, window_seconds=60) is True


class TestAllowByIp:
    def test_ip_scope_uses_request_ip(self, monkeypatch):
        from django.test import RequestFactory

        seen = []
        original = allow_by_identity

        def spy(ident, scope, limit, window_seconds=60):
            seen.append((ident, scope, limit))
            return original(ident, scope, limit, window_seconds)

        monkeypatch.setattr("common.core.throttle.allow_by_identity", spy)
        request = RequestFactory().get("/", REMOTE_ADDR="10.1.2.3")
        assert allow_by_ip(request, scope="unit_ip", limit=1, window_seconds=60) is True
        assert seen == [("10.1.2.3", "unit_ip", 1)]

    def test_missing_ip_uses_unknown(self, monkeypatch):
        from django.test import RequestFactory

        seen = []
        monkeypatch.setattr(
            "common.core.throttle.allow_by_identity",
            lambda ident, **kwargs: (seen.append(ident), True)[1],
        )
        request = RequestFactory().get("/")
        request.META.pop("REMOTE_ADDR", None)
        allow_by_ip(request, scope="unit_ip_unknown", limit=1)
        assert seen == ["unknown"]


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    """限流键落缓存：跨用例清理，避免计数串场。"""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()
