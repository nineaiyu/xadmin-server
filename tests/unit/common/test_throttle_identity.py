# -*- coding: utf-8 -*-
"""固定窗口限流通用入口（allow_by_identity / allow_by_ip）+ DRF 限流类键推导。

allow_by_* 用于非 DRF 场景：Django 原生验证码端点（P0）与 WebSocket 消费者（聊天发送限流）；
DRF 限流类（IpScopedThrottle / ClientScopedThrottle 及其子类）为 O8 安全收口的
匿名凭证端点专用档，此处守护键推导口径，429 行为在集成测试守护。
"""

import pytest
from django.conf import settings

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


class TestIpScopedThrottle:
    """IP 维度专用限流基类：键走 get_request_ip 口径（防 XFF 伪造）。

    限流类只消费 ``request.META`` / ``request.data``，用 stub 隔离 DRF 解析机制。
    """

    @staticmethod
    def _request(meta=None):
        from types import SimpleNamespace

        return SimpleNamespace(META={"REMOTE_ADDR": "10.1.2.3", **(meta or {})})

    @staticmethod
    def _key(throttle, request):
        return throttle.get_cache_key(request, view=None)

    def test_keyed_by_request_ip(self):
        from common.core.throttle import TempTokenThrottle

        assert self._key(TempTokenThrottle(), self._request()) == "throttle_temp_token_10.1.2.3"

    def test_missing_ip_uses_unknown(self):
        from common.core.throttle import VerifyCodeThrottle

        request = self._request()
        request.META.pop("REMOTE_ADDR")
        assert self._key(VerifyCodeThrottle(), request) == "throttle_verify_code_unknown"


class TestClientScopedThrottle:
    """client 维度专用限流基类：凭据即身份；未带 client_id 回退 IP（防随机 id 洗桶）。"""

    @staticmethod
    def _request(data, meta=None):
        from types import SimpleNamespace

        return SimpleNamespace(data=data, META={"REMOTE_ADDR": "127.0.0.1", **(meta or {})})

    @staticmethod
    def _key(throttle, request):
        return throttle.get_cache_key(request, view=None)

    def test_keyed_by_client_id(self):
        from common.core.throttle import OpenClientThrottle

        request = self._request({"client_id": "app_abc", "client_secret": "x"})
        assert self._key(OpenClientThrottle(), request) == "throttle_open_client_client_app_abc"

    def test_missing_client_id_falls_back_to_ip(self):
        from common.core.throttle import OAuthClientThrottle

        assert self._key(OAuthClientThrottle(), self._request({"client_secret": "x"})) == (
            "throttle_oauth_client_ip_127.0.0.1"
        )

    def test_non_dict_body_falls_back_to_ip(self):
        from common.core.throttle import OpenClientThrottle

        assert self._key(OpenClientThrottle(), self._request(None)) == "throttle_open_client_ip_127.0.0.1"

    def test_blank_client_id_falls_back_to_ip(self):
        from common.core.throttle import OpenClientThrottle

        assert self._key(OpenClientThrottle(), self._request({"client_id": "  "})) == (
            "throttle_open_client_ip_127.0.0.1"
        )

    def test_client_and_ip_dimensions_do_not_collide(self):
        from common.core.throttle import OpenClientThrottle

        throttle = OpenClientThrottle()
        client_key = self._key(throttle, self._request({"client_id": "ip_127.0.0.1"}))
        ip_key = self._key(throttle, self._request({}))
        assert client_key != ip_key


def test_o8_throttle_scopes_registered_in_rates():
    """四个 O8 专用档位必须在 DEFAULT_THROTTLE_RATES 登记：漏登记 = 视图挂载即 500。"""
    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    for scope in ("open_client", "oauth_client", "verify_code", "temp_token"):
        assert rates.get(scope), f"throttle scope {scope} not registered"


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    """限流键落缓存：跨用例清理，避免计数串场。"""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()
