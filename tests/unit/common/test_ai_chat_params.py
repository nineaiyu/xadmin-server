# -*- coding: utf-8 -*-
"""ChatCompletionsClient 采样参数全集与重试单元测试。

覆盖：credentials 驱动的请求体（temperature/max_tokens/top_p/penalties/stop/seed）、
None 参数不下发、temperature 兜底、显式 overrides 覆盖、stop 字符串解析、
网络异常与 5xx/429 重试（指数退避）、4xx 不重试、流式建连重试。
"""

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
