# -*- coding: utf-8 -*-
"""EmbeddingClient 单元测试：请求体 / 顺序重排 / 错误归一 / 重试与批次上限。

与 test_ai_chat_params.py 同款假 http 形态（脚本化响应 + 请求留痕），不触外部服务。
"""

import pytest

from common.sdk.ai.chat import AiSdkError
from common.sdk.ai.embeddings import EmbeddingClient

CREDENTIALS = {
    "base_url": "https://ai.example.com/v1",
    "api_key": "sk-test",
    "model": "embed-1",
    "timeout": 10,
    "max_retries": 0,
}


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
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
    monkeypatch.setattr("common.sdk.ai.embeddings.time.sleep", lambda seconds: None)


def _client(http, **overrides):
    return EmbeddingClient({**CREDENTIALS, **overrides}, http_client=http)


class TestEmbed:
    def test_request_body_and_order_restored(self):
        """供应商可乱序返回：按 index 重排，保证与入参顺序一致。"""
        payload = {
            "data": [{"index": 1, "embedding": [0.2, 0.2]}, {"index": 0, "embedding": [0.1, 0.1]}],
            "usage": {"prompt_tokens": 4, "total_tokens": 4},
        }
        http = _FakeHttp([_FakeResponse(payload=payload)])
        client = _client(http)
        vectors = client.embed(["第一段", "第二段"])
        assert vectors == [[0.1, 0.1], [0.2, 0.2]]
        assert http.calls[0]["url"] == "https://ai.example.com/v1/embeddings"
        assert http.calls[0]["json"] == {"model": "embed-1", "input": ["第一段", "第二段"]}
        assert client.last_usage == {"prompt_tokens": 4, "total_tokens": 4}

    def test_empty_input_returns_empty_without_request(self):
        http = _FakeHttp([])
        assert _client(http).embed([]) == []
        assert http.calls == []

    def test_not_configured(self):
        with pytest.raises(AiSdkError, match="not configured"):
            EmbeddingClient({}).embed(["a"])

    def test_rejected_status(self):
        http = _FakeHttp([_FakeResponse(status_code=400, text="bad request")])
        with pytest.raises(AiSdkError, match="rejected"):
            _client(http).embed(["a"])

    def test_invalid_json(self):
        http = _FakeHttp([_FakeResponse(payload="boom")])
        with pytest.raises(AiSdkError, match="invalid response"):
            _client(http).embed(["a"])

    def test_count_mismatch(self):
        payload = {"data": [{"index": 0, "embedding": [0.1]}]}
        http = _FakeHttp([_FakeResponse(payload=payload)])
        with pytest.raises(AiSdkError, match="unexpected embedding count"):
            _client(http).embed(["a", "b"])

    def test_invalid_embedding_payload(self):
        payload = {"data": [{"index": 0, "embedding": ["oops"]}]}
        http = _FakeHttp([_FakeResponse(payload=payload)])
        with pytest.raises(AiSdkError, match="invalid response"):
            _client(http).embed(["a"])

    def test_missing_embedding_payload(self):
        payload = {"data": [{"index": 0}]}
        http = _FakeHttp([_FakeResponse(payload=payload)])
        with pytest.raises(AiSdkError, match="invalid response"):
            _client(http).embed(["a"])

    def test_batch_too_large(self):
        http = _FakeHttp([])
        with pytest.raises(AiSdkError, match="too large"):
            _client(http).embed([f"t{i}" for i in range(300)])
        assert http.calls == []


class TestRetry:
    def test_network_error_retries_then_succeeds(self, no_sleep):
        http = _FakeHttp(
            [
                ConnectionError("boom"),
                _FakeResponse(payload={"data": [{"index": 0, "embedding": [0.5]}]}),
            ]
        )
        client = _client(http, max_retries=1)
        assert client.embed(["a"]) == [[0.5]]
        assert len(http.calls) == 2

    def test_server_error_retries(self, no_sleep):
        http = _FakeHttp(
            [
                _FakeResponse(status_code=502),
                _FakeResponse(payload={"data": [{"index": 0, "embedding": [0.5]}]}),
            ]
        )
        client = _client(http, max_retries=1)
        assert client.embed(["a"]) == [[0.5]]

    def test_client_error_no_retry(self, no_sleep):
        http = _FakeHttp([_FakeResponse(status_code=401, text="unauthorized")])
        client = _client(http, max_retries=3)
        with pytest.raises(AiSdkError):
            client.embed(["a"])
        assert len(http.calls) == 1

    def test_network_error_exhausted(self, no_sleep):
        http = _FakeHttp([ConnectionError("boom") for _ in range(3)])
        client = _client(http, max_retries=2)
        with pytest.raises(AiSdkError, match="Failed to contact"):
            client.embed(["a"])
        assert len(http.calls) == 3
