# -*- coding: utf-8 -*-
"""AsyncChatCompletionsClient 出站守卫单测：生产路径发送前严格校验、语义错误不重试。"""

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


from integrations.sdk.ai.async_chat import AsyncChatCompletionsClient
from integrations.sdk.ai.chat import AiSdkError


def _credentials(base_url="http://10.9.8.7:11434/v1"):
    return {
        "base_url": base_url,
        "api_key": "sk-test",
        "model": "test-model",
        "timeout": 5,
        "max_retries": 0,
        "allowed_hosts": (),
    }


async def test_private_target_blocked_before_send():
    client = AsyncChatCompletionsClient(_credentials())
    with pytest.raises(AiSdkError) as exc:
        await client.chat([{"role": "user", "content": "hi"}])
    assert "outbound policy" in str(exc.value)


async def test_private_target_blocked_for_stream():
    client = AsyncChatCompletionsClient(_credentials())
    with pytest.raises(AiSdkError) as exc:
        async for _event in client.chat_stream([{"role": "user", "content": "hi"}]):
            pass
    assert "outbound policy" in str(exc.value)


async def test_loopback_blocked_when_policy_off(settings):
    """异步流式与同步侧同一放行口径：策略关闭时环回目标发送前即拒绝。"""
    settings.WEBHOOK_ALLOW_LOOPBACK = False
    client = AsyncChatCompletionsClient(_credentials(base_url="http://127.0.0.1:11434/v1"))
    with pytest.raises(AiSdkError) as exc:
        await client.chat([{"role": "user", "content": "hi"}])
    assert "outbound policy" in str(exc.value)


def _install_mock_transport(monkeypatch, handler):
    import httpx

    real_cls = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_cls(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


async def test_domain_target_pinned_to_validated_ip(monkeypatch):
    """DNS rebinding 防护：域名请求连到已校验 IP，Host 与 TLS SNI 保留原域名。"""
    import httpx

    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["host"] = request.headers.get("host")
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("common.utils.outbound.resolve_host_ips", lambda host, resolver=None: ("93.184.216.34",))

    client = AsyncChatCompletionsClient(_credentials("https://ai.example.com/v1"))
    assert await client.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert seen == {
        "url": "https://93.184.216.34/v1/chat/completions",
        "host": "ai.example.com",
        "sni": "ai.example.com",
    }


async def test_stream_domain_target_pinned_to_validated_ip(monkeypatch):
    """流式分支同口径：固定解析目标，避免流式链路在校验后二次解析。"""
    import httpx

    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["host"] = request.headers.get("host")
        seen["sni"] = request.extensions.get("sni_hostname")
        body = 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, content=body.encode())

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("common.utils.outbound.resolve_host_ips", lambda host, resolver=None: ("93.184.216.34",))

    client = AsyncChatCompletionsClient(_credentials("https://ai.example.com/v1"))
    frames = [item async for item in client.chat_stream([{"role": "user", "content": "hi"}])]
    assert frames == [{"type": "content", "text": "ok"}]
    assert seen == {
        "url": "https://93.184.216.34/v1/chat/completions",
        "host": "ai.example.com",
        "sni": "ai.example.com",
    }
