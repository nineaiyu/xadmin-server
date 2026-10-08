# -*- coding: utf-8 -*-
"""OAuth / OIDC 出站链路统一口径（写入侧校验 + 发送侧固定解析连接）。

背景：provider URL 此前是独立实现的 https 前缀判断，与 Webhook / AI base_url /
MCP / 开放平台回调的出站守卫（`packages/xadmin-common/common/utils/outbound.py`）分叉——私网 / 元数据
地址字面量与「校验一次解析、连接又解析一次」的 DNS rebinding 窗口都不在该链路
覆盖内。本文件锁定统一后的双向口径，以及「注入客户端不触发守卫」的离线桩契约。
"""

import pytest
from django.core.exceptions import ValidationError

from common.utils.outbound import OutboundBlocked
from identity.utils.oauth import _get, _post, validate_providers
from identity.utils.oauth_flavors import _post_json

PROVIDER = {
    "key": "stub-idp",
    "name": "Stub IdP",
    "client_id": "client-1",
    "client_secret": "secret-1",
    "authorize_url": "https://idp.example.com/authorize",
    "token_url": "https://idp.example.com/token",
    "userinfo_url": "https://idp.example.com/userinfo",
}


class TestProviderUrlWriteSide:
    """写入侧：https 强制 + 地址归属校验（域名写入侧不解析，留到发送侧）。"""

    def test_https_domain_accepted(self):
        assert validate_providers([dict(PROVIDER)])[0]["key"] == "stub-idp"

    def test_plain_http_remote_rejected(self):
        with pytest.raises(ValidationError):
            validate_providers([dict(PROVIDER, token_url="http://idp.example.com/token")])

    def test_loopback_http_accepted_for_local_testing(self):
        """统一口径的 loopback 例外：本地联调 IdP 可走 http（与 Webhook 一致）。"""
        config = validate_providers([dict(PROVIDER, token_url="http://127.0.0.1:9000/token")])
        assert config[0]["token_url"] == "http://127.0.0.1:9000/token"

    @pytest.mark.parametrize("url", ["https://10.0.0.8/token", "https://169.254.169.254/token"])
    def test_private_or_metadata_literal_rejected(self, url):
        with pytest.raises(ValidationError):
            validate_providers([dict(PROVIDER, token_url=url)])


class _RecordingClient:
    """离线桩：记录入参并返回哨兵对象。"""

    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return {"client": "stub"}

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        return {"client": "stub"}


class TestSendSideGuard:
    """发送侧：生产路径（无注入客户端）走统一守卫 + 固定解析连接。"""

    @pytest.mark.parametrize("url", ["https://10.0.0.8/token", "https://169.254.169.254/token"])
    def test_private_target_rejected(self, url):
        with pytest.raises(OutboundBlocked):
            _get(url, {})

    def test_metadata_target_rejected_on_post(self):
        with pytest.raises(OutboundBlocked):
            _post("https://169.254.169.254/token", {})

    def test_flavor_helper_shares_guard(self):
        """IM flavor 适配器的出站助手与通用链路同源（同一守卫 + 固定连接）。"""
        with pytest.raises(OutboundBlocked):
            _post_json("https://10.0.0.8/token", {})

    def test_injected_client_bypasses_guard(self):
        """离线桩（测试注入）保持原样调用：不解析、不固定连接，单测零网络依赖。"""
        client = _RecordingClient()
        assert _get("https://10.0.0.8/token", {}, http_client=client) == {"client": "stub"}
        assert client.calls[0][0] == "get"
