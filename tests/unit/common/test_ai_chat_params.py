# -*- coding: utf-8 -*-
"""ChatCompletionsClient 采样参数全集与重试单元测试。

覆盖：credentials 驱动的请求体（temperature/max_tokens/top_p/penalties/stop/seed）、
None 参数不下发、temperature 兜底、显式 overrides 覆盖、stop 字符串解析、
网络异常与 5xx/429 重试（指数退避）、4xx 不重试、流式建连重试、流式 UTF-8 解码。
"""

import json

import pytest

from common.sdk.ai.chat import AiSdkError, ChatCompletionsClient

FULL_CREDENTIALS = {
    "base_url": "https://ai.example.com/v1",
    "api_key": "sk-test",
    "model": "test-model",
    "timeout": 30,
    "max_retries": 0,
    "temperature": 0.7,
    "max_tokens": 2048,
    "top_p": 0.9,
    "frequency_penalty": 0.5,
    "presence_penalty": -0.5,
    "seed": 42,
    "stop": ["END", "STOP"],
}


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        if self._payload == "boom":
            raise ValueError("invalid json")
        return self._payload


class _FakeHttp:
    """记录请求的假 http：按脚本逐次返回响应或抛异常。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        action = self.script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr("common.sdk.ai.chat.time.sleep", lambda seconds: None)


class TestRequestBody:
    def test_full_params_forwarded(self):
        body = ChatCompletionsClient(FULL_CREDENTIALS)._body([{"role": "user", "content": "hi"}])
        assert body["model"] == "test-model"
        assert body["temperature"] == 0.7
        assert body["max_tokens"] == 2048
        assert body["top_p"] == 0.9
        assert body["frequency_penalty"] == 0.5
        assert body["presence_penalty"] == -0.5
        assert body["seed"] == 42
        assert body["stop"] == ["END", "STOP"]
        assert "stream" not in body

    def test_none_params_omitted_and_temperature_defaults(self):
        client = ChatCompletionsClient({"base_url": "u", "api_key": "k", "model": "m"})
        body = client._body([])
        assert body["temperature"] == 0.2
        for key in ("max_tokens", "top_p", "frequency_penalty", "presence_penalty", "seed", "stop", "stream"):
            assert key not in body

    def test_stream_flag_and_overrides(self):
        client = ChatCompletionsClient(FULL_CREDENTIALS)
        body = client._body([], stream=True, temperature=0.1)
        assert body["stream"] is True
        assert body["temperature"] == 0.1  # 显式覆盖优先
        body2 = client._body([], temperature=None)  # None 覆盖被忽略
        assert body2["temperature"] == 0.7

    def test_stop_string_parsed(self):
        client = ChatCompletionsClient({"base_url": "u", "api_key": "k", "model": "m", "stop": " A , B ,, "})
        assert client.stop == ["A", "B"]
        assert ChatCompletionsClient({"base_url": "u", "api_key": "k", "model": "m", "stop": ""}).stop == []

    def test_max_tokens_zero_omitted(self):
        client = ChatCompletionsClient({"base_url": "u", "api_key": "k", "model": "m", "max_tokens": 0})
        assert "max_tokens" not in client._body([])


class TestChat:
    def test_chat_returns_content(self):
        http = _FakeHttp([_FakeResponse(payload={"choices": [{"message": {"content": "hi there"}}]})])
        client = ChatCompletionsClient(FULL_CREDENTIALS, http_client=http)
        assert client.chat([{"role": "user", "content": "ping"}]) == "hi there"
        request_body = http.calls[0]["json"]
        assert request_body["temperature"] == 0.7 and request_body["model"] == "test-model"

    def test_invalid_json_readable_error(self):
        http = _FakeHttp([_FakeResponse(payload="boom")])
        client = ChatCompletionsClient(FULL_CREDENTIALS, http_client=http)
        with pytest.raises(AiSdkError, match="invalid response"):
            client.chat([])

    def test_not_configured(self):
        with pytest.raises(AiSdkError, match="not configured"):
            ChatCompletionsClient({}).chat([])


class TestRetry:
    def test_network_error_retries_then_succeeds(self, no_sleep):
        http = _FakeHttp(
            [
                ConnectionError("boom"),
                ConnectionError("boom"),
                _FakeResponse(payload={"choices": [{"message": {"content": "ok"}}]}),
            ]
        )
        client = ChatCompletionsClient({**FULL_CREDENTIALS, "max_retries": 2}, http_client=http)
        assert client.chat([]) == "ok"
        assert len(http.calls) == 3

    def test_network_error_exhausted(self, no_sleep):
        http = _FakeHttp([ConnectionError("boom") for _ in range(3)])
        client = ChatCompletionsClient({**FULL_CREDENTIALS, "max_retries": 2}, http_client=http)
        with pytest.raises(AiSdkError, match="Failed to contact"):
            client.chat([])
        assert len(http.calls) == 3

    def test_server_error_retries(self, no_sleep):
        http = _FakeHttp(
            [_FakeResponse(status_code=502), _FakeResponse(payload={"choices": [{"message": {"content": "ok"}}]})]
        )
        client = ChatCompletionsClient({**FULL_CREDENTIALS, "max_retries": 1}, http_client=http)
        assert client.chat([]) == "ok"

    def test_rate_limit_retries(self, no_sleep):
        http = _FakeHttp(
            [_FakeResponse(status_code=429), _FakeResponse(payload={"choices": [{"message": {"content": "ok"}}]})]
        )
        client = ChatCompletionsClient({**FULL_CREDENTIALS, "max_retries": 1}, http_client=http)
        assert client.chat([]) == "ok"

    def test_client_error_no_retry(self, no_sleep):
        http = _FakeHttp([_FakeResponse(status_code=401, payload={})])
        client = ChatCompletionsClient({**FULL_CREDENTIALS, "max_retries": 3}, http_client=http)
        with pytest.raises(AiSdkError, match="empty answer"):
            client.chat([])
        assert len(http.calls) == 1

    def test_no_retry_by_default(self, no_sleep):
        http = _FakeHttp([ConnectionError("boom")])
        client = ChatCompletionsClient(FULL_CREDENTIALS, http_client=http)
        with pytest.raises(AiSdkError):
            client.chat([])
        assert len(http.calls) == 1

    def test_stream_retry_before_first_byte(self, no_sleep):
        http = _FakeHttp([ConnectionError("boom"), _FakeResponse(payload={})])
        client = ChatCompletionsClient({**FULL_CREDENTIALS, "max_retries": 1}, http_client=http)
        response = client._post(
            "https://ai.example.com/v1/chat/completions", {"model": "m", "messages": []}, stream=True
        )
        assert response.status_code == 200
        assert len(http.calls) == 2
        assert http.calls[-1]["stream"] is True


class TestStreamDecoding:
    """流式增量固定按 UTF-8 解码。

    回归背景：SSE 响应头 `text/event-stream` 不带 charset 时，requests 会把编码
    推断成 ISO-8859-1，旧实现 ``iter_lines(decode_unicode=True)`` 让中文增量全部
    mojibake（数据库 → æ°æ®åº）；这里用 bytes 行（真实 requests 路径）与预解码
    str 行（测试桩可能返回 str）两种形态验证。
    """

    @staticmethod
    def _stream_response(lines, status_code=200):
        class _StreamResponse:
            text = ""

            def __init__(self):
                self.status_code = status_code

            def iter_lines(self, *args, **kwargs):
                yield from lines

        return _StreamResponse()

    def _client_with_lines(self, lines):
        http = _FakeHttp([self._stream_response(lines)])
        return ChatCompletionsClient(FULL_CREDENTIALS, http_client=http)

    @staticmethod
    def _texts(events):
        """事件序列 → 正文文本（content 类型）。"""
        return "".join(item["text"] for item in events if item["type"] == "content")

    def test_chinese_bytes_decoded_as_utf8(self):
        chunks = ["数据库", "是", "什么"]
        # bytes 行（str.encode 默认 UTF-8）：贴近真实 requests iter_lines 的返回形态
        lines = [
            f"data: {json.dumps({'choices': [{'delta': {'content': c}}]}, ensure_ascii=False)}".encode() for c in chunks
        ]
        lines.append(b"data: [DONE]")
        lines.append(b"")
        events = list(self._client_with_lines(lines).chat_stream([]))
        assert self._texts(events) == "数据库是什么"
        assert all(item["type"] == "content" for item in events)

    def test_predecoded_str_lines_supported(self):
        lines = ['data: {"choices": [{"delta": {"content": "你好"}}]}', "", "data: [DONE]", ""]
        assert self._texts(list(self._client_with_lines(lines).chat_stream([]))) == "你好"

    def test_reasoning_content_emitted_separately(self):
        """思考型模型：delta.reasoning_content 产出为 reasoning 事件（与正文分开）。"""
        lines = [
            'data: {"choices": [{"delta": {"reasoning_content": "先想"}}]}',
            'data: {"choices": [{"delta": {"reasoning_content": "再看"}}]}',
            'data: {"choices": [{"delta": {"content": "答案"}}]}',
            "data: [DONE]",
            "",
        ]
        events = list(self._client_with_lines(lines).chat_stream([]))
        assert [item["type"] for item in events] == ["reasoning", "reasoning", "content"]
        assert "".join(item["text"] for item in events if item["type"] == "reasoning") == "先想再看"
        assert self._texts(events) == "答案"

    def test_only_reasoning_is_not_empty_answer(self):
        """只有思考没有回答不算「空回答」（已有增量），由调用方决定展示口径。"""
        lines = ['data: {"choices": [{"delta": {"reasoning_content": "一直在想"}}]}', "data: [DONE]", ""]
        events = list(self._client_with_lines(lines).chat_stream([]))
        assert [item["type"] for item in events] == ["reasoning"]


class TestChatReasoningCapture:
    """非流式 chat()：reasoning_content 采集与「只思考未回答」的可读区分。"""

    def test_chat_records_reasoning(self):
        payload = {
            "choices": [{"message": {"content": "答案", "reasoning_content": "思考过程"}}],
        }
        http = _FakeHttp([_FakeResponse(payload=payload)])
        client = ChatCompletionsClient(FULL_CREDENTIALS, http_client=http)
        assert client.chat([]) == "答案"
        assert client.last_reasoning == "思考过程"

    def test_chat_only_reasoning_error_message(self):
        payload = {"choices": [{"message": {"content": "", "reasoning_content": "想了很久"}}]}
        http = _FakeHttp([_FakeResponse(payload=payload)])
        client = ChatCompletionsClient(FULL_CREDENTIALS, http_client=http)
        with pytest.raises(AiSdkError, match="only reasoning content"):
            client.chat([])
        assert client.last_reasoning == "想了很久"

    def test_chat_empty_answer_without_reasoning(self):
        payload = {"choices": [{"message": {"content": ""}}]}
        http = _FakeHttp([_FakeResponse(payload=payload)])
        client = ChatCompletionsClient(FULL_CREDENTIALS, http_client=http)
        with pytest.raises(AiSdkError, match="empty answer"):
            client.chat([])
        assert client.last_reasoning is None


class TestUsageCapture:
    """usage 采集：chat() 成功时记录供应商返回的 token 用量（供审计与成本观测）。"""

    def test_chat_records_usage(self):
        usage = {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
        http = _FakeHttp([_FakeResponse(payload={"choices": [{"message": {"content": "hi"}}], "usage": usage})])
        client = ChatCompletionsClient(FULL_CREDENTIALS, http_client=http)
        assert client.chat([]) == "hi"
        assert client.last_usage == usage

    def test_chat_without_usage_keeps_none(self):
        http = _FakeHttp([_FakeResponse(payload={"choices": [{"message": {"content": "hi"}}]})])
        client = ChatCompletionsClient(FULL_CREDENTIALS, http_client=http)
        client.chat([])
        assert client.last_usage is None

    def test_invalid_usage_shape_ignored(self):
        http = _FakeHttp([_FakeResponse(payload={"choices": [{"message": {"content": "hi"}}], "usage": "oops"})])
        client = ChatCompletionsClient(FULL_CREDENTIALS, http_client=http)
        client.chat([])
        assert client.last_usage is None
