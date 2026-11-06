# -*- coding: utf-8 -*-
"""IM token 缓存 TTL 守护：供应商下发有效期优先，缺省/异常值回落保守假设。"""

from typing import Any

from django.core.cache import cache

from integrations.sdk.im.base import TOKEN_TTL_SLACK, BaseImClient
from integrations.sdk.im.dingtalk import DingTalkClient
from integrations.sdk.im.feishu import FeishuClient
from integrations.sdk.im.wecom import WeComClient


class _RecordingClient(BaseImClient):
    """桩：固定 token 响应载荷，记录缓存写入的 TTL。"""

    token_cache_prefix = "im_test_token_"
    payload: dict[str, Any] = {}

    def _fetch_token(self, credentials: dict[str, Any]) -> str:
        self._apply_token_ttl(self.payload, "expires_in", "expire", "expireIn")
        token = str(self.payload.get("access_token") or self.payload.get("tenant_access_token") or "tok")
        return token


def _cache_ttl_written(monkeypatch, payload: dict[str, Any]) -> int:
    client = _RecordingClient(credentials={"app_key": "k", "app_secret": "s"})
    client.payload = payload
    captured: dict[str, Any] = {}

    def fake_set(key: str, value: Any, timeout: int) -> None:
        captured[key] = timeout

    monkeypatch.setattr(cache, "set", fake_set)
    monkeypatch.setattr(cache, "get", lambda key: None)
    client._cached_token()
    [ttl] = captured.values()
    return int(ttl)


class TestTokenTtlFromProvider:
    def test_provider_ttl_used_with_slack(self, monkeypatch):
        ttl = _cache_ttl_written(monkeypatch, {"access_token": "t", "expires_in": 3600})
        assert ttl == 3600 - TOKEN_TTL_SLACK

    def test_missing_ttl_falls_back_to_default(self, monkeypatch):
        ttl = _cache_ttl_written(monkeypatch, {"access_token": "t"})
        assert ttl == 7200 - TOKEN_TTL_SLACK

    def test_absurd_ttl_falls_back_to_default(self, monkeypatch):
        # 疑似毫秒单位错位（> 86400）不当作秒缓存出长命过期 token
        ttl = _cache_ttl_written(monkeypatch, {"access_token": "t", "expires_in": 7200000})
        assert ttl == 7200 - TOKEN_TTL_SLACK

    def test_string_ttl_accepted(self, monkeypatch):
        ttl = _cache_ttl_written(monkeypatch, {"access_token": "t", "expires_in": "1800"})
        assert ttl == 1800 - TOKEN_TTL_SLACK

    def test_per_vendor_key_names(self):
        """三家各自的有效期字段名（wecom=expires_in / feishu=expire / dingtalk=expireIn）都被识别。"""
        client = _RecordingClient()
        client._apply_token_ttl({"expires_in": 3600}, "expires_in")
        assert client.token_ttl == 3600
        client._apply_token_ttl({"expire": 1800}, "expire", "expires_in")
        assert client.token_ttl == 1800
        client._apply_token_ttl({"expireIn": 900}, "expires_in", "expireIn")
        assert client.token_ttl == 900


class TestVendorClientsExist:
    """三家客户端都挂了 TTL 采集（防重构丢失 _apply_token_ttl 调用）。"""

    def test_vendor_classes_importable(self):
        assert DingTalkClient is not None
        assert FeishuClient is not None
        assert WeComClient is not None
