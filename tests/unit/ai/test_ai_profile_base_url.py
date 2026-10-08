# -*- coding: utf-8 -*-
"""AI 档案 base_url 出站守卫单测（与 Webhook/MCP 同口径）。

覆盖：https 放行（含内网域名——写入侧不解析、发送侧严格校验）、http 仅 loopback
或 OUTBOUND_ALLOWED_HOSTS 登记目标、私网字面量任何协议拒绝、协议前缀校验。
"""

import pytest
from rest_framework.exceptions import ValidationError

from ai.serializers.ai import AiProfileSerializer


@pytest.fixture(autouse=True)
def _empty_whitelist(monkeypatch):
    monkeypatch.setattr("ai.serializers.ai.outbound_allowed_hosts", lambda: ())
    yield


class TestValidateBaseUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://api.example.com/v1",
            "https://internal.corp.example/v1",
            "http://127.0.0.1:11434/v1",
            "http://localhost:11434/v1",
        ],
    )
    def test_allowed_targets(self, url):
        assert AiProfileSerializer().validate_base_url(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            "http://192.168.1.5:11434/v1",  # 内网 http 未登记
            "http://host.docker.internal:1234/v1",  # 本机容器场景也须登记
            "https://10.0.0.8/v1",  # 私网字面量任何协议都拒绝
            "ftp://ai.example.com/v1",
            "not-a-url",
            "",
        ],
    )
    def test_rejected_targets(self, url):
        with pytest.raises(ValidationError):
            AiProfileSerializer().validate_base_url(url)

    def test_whitelisted_http_target_allowed(self, monkeypatch):
        monkeypatch.setattr("ai.serializers.ai.outbound_allowed_hosts", lambda: ("host.docker.internal",))
        url = "http://host.docker.internal:1234/v1"
        assert AiProfileSerializer().validate_base_url(url) == url

    def test_whitelisted_private_ip_allowed(self, monkeypatch):
        monkeypatch.setattr("ai.serializers.ai.outbound_allowed_hosts", lambda: ("192.168.1.5",))
        url = "http://192.168.1.5:11434/v1"
        assert AiProfileSerializer().validate_base_url(url) == url
