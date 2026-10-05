# -*- coding: utf-8 -*-
"""AsyncChatCompletionsClient 单元测试（假异步 http 客户端，不触网络）。

覆盖：流式增量事件序（reasoning/content/[DONE]）、UTF-8 字节行解码、非 200 拒绝、
流中断转 AiSdkError、chat/chat_tools 报文解析与 usage 采集、5xx 重试（指数退避）。
"""

import json

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


from integrations.sdk.ai.async_chat import AsyncChatCompletionsClient
from integrations.sdk.ai.chat import AiSdkError

CREDENTIALS = {
    "base_url": "https://ai.example.com/v1",
    "api_key": "sk-test",
    "model": "async-model",
    "timeout": 30,
    "max_retries": 1,
}


class _FakeStreamResponse:
    def __init__(self, status_code=200, lines=(), text=""):
        self.status_code = status_code
        self._lines = list(lines)
        self.text = text

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _StreamCM:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc_info):
        return False


class _FakeAsyncHttp:
    """与 httpx.AsyncClient 同构的假客户端：post / stream（异步上下文管理器）。"""

    def __init__(self, stream_lines=(), status_code=200, post_payload=None, post_status=200):
        self.stream_lines = list(stream_lines)
        self.stream_status = status_code
        self.post_payload = post_payload or {}
        self.post_status = post_status
        self.stream_calls = 0
        self.post_calls = 0

    def stream(self, method, url, timeout=None, json=None, headers=None):
        self.stream_calls += 1
        return _StreamCM(_FakeStreamResponse(status_code=self.stream_status, lines=self.stream_lines))

    async def post(self, url, timeout=None, json=None, headers=None):
        self.post_calls += 1
        return _FakeResponse(self.post_status, self.post_payload)


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}"


async def test_chat_stream_yields_events_in_order():
    http = _FakeAsyncHttp(
        stream_lines=[
            _sse({"choices": [{"delta": {"reasoning_content": "思考中"}}]}).encode(),
            b"",
            _sse({"choices": [{"delta": {"content": "你好"}}]}),
            _sse({"choices": [{"delta": {}}]}).encode("utf-8"),
            "data: [DONE]",
        ]
    )
    client = AsyncChatCompletionsClient(CREDENTIALS, http_client=http)
    events = [item async for item in client.chat_stream([{"role": "user", "content": "hi"}])]
    assert events == [
        {"type": "reasoning", "text": "思考中"},
        {"type": "content", "text": "你好"},
    ]


async def test_chat_stream_non_200_rejected():
    http = _FakeAsyncHttp(stream_lines=[b"oops"], status_code=503)
    client = AsyncChatCompletionsClient(CREDENTIALS, http_client=http)
    with pytest.raises(AiSdkError, match="rejected the streaming request"):
        [item async for item in client.chat_stream([{"role": "user", "content": "hi"}])]


async def test_chat_stream_interruption_raises():
    class _Broken:
        status_code = 200

        async def aiter_lines(self):
            yield _sse({"choices": [{"delta": {"content": "部分"}}]})
            raise ConnectionError("dropped")

    class _BrokenHttp:
        def stream(self, method, url, timeout=None, json=None, headers=None):
            return _StreamCM(_Broken())

    client = AsyncChatCompletionsClient(CREDENTIALS, http_client=_BrokenHttp())
    with pytest.raises(AiSdkError, match="interrupted"):
        [item async for item in client.chat_stream([{"role": "user", "content": "hi"}])]


async def test_chat_stream_empty_answer():
    http = _FakeAsyncHttp(stream_lines=[_sse({"choices": [{"delta": {}}]}), "data: [DONE]"])
    client = AsyncChatCompletionsClient(CREDENTIALS, http_client=http)
    with pytest.raises(AiSdkError, match="empty answer"):
        [item async for item in client.chat_stream([{"role": "user", "content": "hi"}])]


async def test_chat_parses_message_and_usage():
    usage = {"prompt_tokens": 5, "total_tokens": 9}
    http = _FakeAsyncHttp(
        post_payload={
            "choices": [{"message": {"content": "答案", "reasoning_content": "想"}}],
            "usage": usage,
        }
    )
    client = AsyncChatCompletionsClient(CREDENTIALS, http_client=http)
    answer = await client.chat([{"role": "user", "content": "hi"}])
    assert answer == "答案"
    assert client.last_usage == usage
    assert client.last_reasoning == "想"


async def test_chat_5xx_retries_then_succeeds(monkeypatch):
    class _FlakyHttp(_FakeAsyncHttp):
        def __init__(self):
            super().__init__(post_payload={"choices": [{"message": {"content": "ok"}}]})
            self.flakes = 1

        async def post(self, url, timeout=None, json=None, headers=None):
            self.post_calls += 1
            if self.flakes:
                self.flakes -= 1
                return _FakeResponse(503, {})
            return _FakeResponse(200, self.post_payload)

    monkeypatch.setattr("integrations.sdk.ai.async_chat.asyncio.sleep", _async_noop)
    http = _FlakyHttp()
    client = AsyncChatCompletionsClient(CREDENTIALS, http_client=http)
    assert await client.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert http.post_calls == 2


async def test_chat_tools_normalizes_calls():
    payload = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [{"id": "c1", "function": {"name": "search", "arguments": {"q": "数据集"}}}],
                }
            }
        ]
    }
    http = _FakeAsyncHttp(post_payload=payload)
    client = AsyncChatCompletionsClient(CREDENTIALS, http_client=http)
    result = await client.chat_tools(
        [{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "search"}}]
    )
    assert result["content"] == ""
    assert result["tool_calls"] == [{"id": "c1", "name": "search", "arguments": '{"q": "数据集"}'}]


async def _async_noop(_seconds):
    return None


async def test_stream_self_built_client_5xx_retry_reads_status(monkeypatch):
    """自建客户端分支（未注入 http_client）：流式包装器必须暴露 status_code。

    回归（e2e 实测）：_OwnedStream 缺 status_code 时 _send_with_retry 的
    5xx/429 判定在首轮即 AttributeError，流式问答整链瘫痪。
    """
    import httpx

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500)
        body = 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, content=body.encode())

    real_cls = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_cls(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    monkeypatch.setattr("integrations.sdk.ai.async_chat._RETRY_BASE_DELAY", 0)

    client = AsyncChatCompletionsClient(CREDENTIALS)
    frames = [item async for item in client.chat_stream([{"role": "user", "content": "hi"}])]
    assert calls["n"] == 2  # 500 一次 + 成功一次：重试判定读到状态码后才可能重试
    assert frames[-1] == {"type": "content", "text": "ok"}
