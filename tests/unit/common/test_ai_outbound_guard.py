# -*- coding: utf-8 -*-
"""AI SDK 出站守卫单测：生产路径（未注入 http）默认拒私网、白名单透传、注入桩跳过。

与 Webhook/MCP 同口径：私网目标默认拒绝（loopback 供本地联调），域名固定为已校验
IP 连接；出站拒绝属配置类错误（AiSdkError，不触发重试、不发起网络请求）。
"""

import pytest

from integrations.sdk.ai.chat import AiSdkError, ChatCompletionsClient
from integrations.sdk.ai.embeddings import EmbeddingClient

PRIVATE_BASE = "http://10.9.8.7:11434/v1"


def _credentials(base_url=PRIVATE_BASE, allowed=()):
    return {
        "base_url": base_url,
        "api_key": "sk-test",
        "model": "test-model",
        "timeout": 5,
        "max_retries": 0,
        "allowed_hosts": allowed,
    }


class _StubResponse:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class TestSyncChatOutboundGuard:
    def test_private_target_blocked(self):
        client = ChatCompletionsClient(_credentials())
        with pytest.raises(AiSdkError) as exc:
            client.chat([{"role": "user", "content": "hi"}])
        assert "outbound policy" in str(exc.value)

    def test_whitelisted_target_uses_pinned_request(self, monkeypatch):
        """白名单命中：路由到固定解析连接路径并透传白名单，响应正常解析。"""
        called = {}

        def fake_post(url, kwargs, *, allowed_hosts=None):
            called["url"] = url
            called["allowed_hosts"] = allowed_hosts
            return _StubResponse({"choices": [{"message": {"content": "ok"}}]})

        monkeypatch.setattr("integrations.sdk.ai.chat.outbound_pinned_post", fake_post)
        client = ChatCompletionsClient(_credentials(allowed=("10.9.8.7",)))
        assert client.chat([{"role": "user", "content": "hi"}]) == "ok"
        assert called["url"] == f"{PRIVATE_BASE}/chat/completions"
        assert called["allowed_hosts"] == ("10.9.8.7",)

    def test_injected_http_skips_guard(self):
        """注入 http（测试桩）：不做任何出站校验（既有测试面零影响）。"""

        class _Http:
            def post(self, url, **kwargs):
                return _StubResponse({"choices": [{"message": {"content": "stub"}}]})

        client = ChatCompletionsClient(_credentials(), http_client=_Http())
        assert client.chat([]) == "stub"


class TestEmbeddingOutboundGuard:
    def test_private_target_blocked(self):
        client = EmbeddingClient(_credentials())
        with pytest.raises(AiSdkError) as exc:
            client.embed(["hello"])
        assert "outbound policy" in str(exc.value)


class TestLoopbackPolicy:
    """环回放行跟随 ``DEBUG`` / ``WEBHOOK_ALLOW_LOOPBACK``（与 Webhook / 回调测试同源）。

    历史实现硬编码 ``allow_loopback=True``：生产环境持 AI 档案配置权限的账号可借
    base_url 探测本机内部服务（测试连接接口即可验证连通性）。
    """

    LOOPBACK_BASE = "http://127.0.0.1:11434/v1"

    @pytest.mark.parametrize("allowed", [False, True])
    def test_flag_follows_policy(self, monkeypatch, settings, allowed):
        seen = {}

        def fake_pinned_request(method, url, **kwargs):
            seen["allow_loopback"] = kwargs.get("allow_loopback")
            return _StubResponse({"choices": [{"message": {"content": "ok"}}]})

        settings.WEBHOOK_ALLOW_LOOPBACK = allowed
        monkeypatch.setattr("common.utils.outbound.pinned_request", fake_pinned_request)
        client = ChatCompletionsClient(_credentials(base_url=self.LOOPBACK_BASE))

        assert client.chat([{"role": "user", "content": "hi"}]) == "ok"
        assert seen["allow_loopback"] is allowed

    def test_loopback_blocked_when_policy_off(self, settings):
        """策略关闭（生产默认）：环回目标在发送侧被守卫拒绝，不发起请求。"""
        settings.WEBHOOK_ALLOW_LOOPBACK = False
        client = ChatCompletionsClient(_credentials(base_url=self.LOOPBACK_BASE))
        with pytest.raises(AiSdkError) as exc:
            client.chat([{"role": "user", "content": "hi"}])
        assert "outbound policy" in str(exc.value)
