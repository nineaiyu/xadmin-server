# -*- coding: utf-8 -*-
"""MCP 客户端（外接 MCP server）单测：出站 URL 守卫 / 响应解析 / 快照与结果摘要。"""

import json

import pytest
from django.core.exceptions import ValidationError

from ai.utils import mcp_client as mcp


@pytest.fixture(autouse=True)
def _empty_whitelist(monkeypatch):
    monkeypatch.setattr(mcp, "outbound_allowed_hosts", lambda: ())
    yield


class TestValidateServerUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://mcp.example.com/mcp",
            "http://127.0.0.1:8765/mcp",
            "http://localhost:8765/mcp",
        ],
    )
    def test_allowed_targets(self, url):
        assert mcp.validate_server_url(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            "http://192.168.1.10:8765/mcp",  # 内网 http 未白名单
            "http://mcp.internal/mcp",
            "ftp://mcp.example.com/mcp",
            "https://",  # 缺 host
            "",
        ],
    )
    def test_rejected_targets(self, url):
        with pytest.raises(ValidationError):
            mcp.validate_server_url(url)

    def test_whitelisted_intranet_http_allowed(self, monkeypatch):
        monkeypatch.setattr(mcp, "outbound_allowed_hosts", lambda: ("mcp.internal",))
        assert mcp.validate_server_url("http://mcp.internal:8765/mcp") == "http://mcp.internal:8765/mcp"


class _FakeResponse:
    """pinned_request 的最小响应替身（JSON / SSE 两形态）。"""

    def __init__(self, *, payload: bytes = b"", lines=(), status_code: int = 200, headers=None):
        self._payload = payload
        self._lines = list(lines)
        self.status_code = status_code
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_lines(self, decode_unicode=False):
        for line in self._lines:
            yield line if isinstance(line, bytes) else line.encode("utf-8")

    def iter_content(self, chunk_size=65536):
        yield self._payload


def _client(monkeypatch, response) -> mcp.McpClient:
    monkeypatch.setattr(mcp, "pinned_request", lambda *args, **kwargs: response)
    return mcp.McpClient("http://127.0.0.1:9/mcp")


class TestResponseParsing:
    def test_json_response(self, monkeypatch):
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}).encode()
        client = _client(monkeypatch, _FakeResponse(payload=payload, headers={"Content-Type": "application/json"}))
        assert client._call("tools/list") == {"ok": True}

    def test_sse_response(self, monkeypatch):
        line = b'data: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}'
        response = _FakeResponse(lines=[b"event: message", line, b""], headers={"Content-Type": "text/event-stream"})
        client = _client(monkeypatch, response)
        assert client._call("tools/list") == {"tools": []}

    def test_rpc_error_raises(self, monkeypatch):
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "boom"}}).encode()
        client = _client(monkeypatch, _FakeResponse(payload=payload, headers={"Content-Type": "application/json"}))
        with pytest.raises(mcp.McpClientError):
            client._call("tools/list")

    def test_http_error_raises(self, monkeypatch):
        client = _client(monkeypatch, _FakeResponse(status_code=500, headers={"Content-Type": "application/json"}))
        with pytest.raises(mcp.McpClientError):
            client._call("tools/list")

    def test_invalid_json_raises(self, monkeypatch):
        client = _client(monkeypatch, _FakeResponse(payload=b"not json", headers={"Content-Type": "application/json"}))
        with pytest.raises(mcp.McpClientError):
            client._call("tools/list")

    def test_oversized_body_raises(self, monkeypatch):
        monkeypatch.setattr(mcp, "MAX_BODY_BYTES", 4)
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode()
        client = _client(monkeypatch, _FakeResponse(payload=payload, headers={"Content-Type": "application/json"}))
        with pytest.raises(mcp.McpClientError):
            client._call("tools/list")

    def test_session_id_captured(self, monkeypatch):
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode()
        headers = {"Content-Type": "application/json", "Mcp-Session-Id": "session-1"}
        client = _client(monkeypatch, _FakeResponse(payload=payload, headers=headers))
        client._call("initialize")
        assert client.session_id == "session-1"
        assert client._headers()["Mcp-Session-Id"] == "session-1"

    def test_blocked_target_raises_client_error(self, monkeypatch):
        from common.utils.outbound import OutboundBlocked

        def _blocked(*args, **kwargs):
            raise OutboundBlocked(["blocked"])

        monkeypatch.setattr(mcp, "pinned_request", _blocked)
        client = mcp.McpClient("http://127.0.0.1:9/mcp")
        with pytest.raises(mcp.McpClientError):
            client._call("tools/list")


class TestToolSummary:
    def test_summary_keeps_display_fields(self):
        summary = mcp.McpClient._tool_summary(
            {
                "name": "echo",
                "description": "Echo text",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}, "count": {}},
                    "required": ["text"],
                },
                "annotations": {"readOnlyHint": True},
            }
        )
        assert summary == {
            "name": "echo",
            "description": "Echo text",
            "read_only": True,
            "params": ["text", "count"],
            "required": ["text"],
            # F3：快照补有界 input_schema（白名单关键字 + additionalProperties 收口）
            "input_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "count": {}},
                "required": ["text"],
                "additionalProperties": False,
            },
            "schema_truncated": False,
        }

    def test_summary_missing_input_schema_keeps_empty(self):
        """无 inputSchema 的工具：input_schema 为空 dict（动作目录侧对其 fail-closed 跳过）。"""
        summary = mcp.McpClient._tool_summary({"name": "bare"})
        assert summary["input_schema"] == {}
        assert summary["schema_truncated"] is False

    def test_summarize_tool_result_truncates(self):
        result = {"content": [{"type": "text", "text": "x" * 10}], "isError": False}
        summary = mcp.summarize_tool_result(result, limit=4)
        assert summary == {"is_error": False, "text": "xxxx..."}
        assert mcp.summarize_tool_result({"isError": True, "content": []})["is_error"] is True
