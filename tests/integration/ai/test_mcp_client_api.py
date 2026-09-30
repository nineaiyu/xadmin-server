# -*- coding: utf-8 -*-
"""外部 MCP 服务器（MCP client）集成测试：本地桩服务器全链路。

覆盖：配置 CRUD（令牌只进不出 / 内网 http 拒绝）、tools/list 同步（快照与会话头）、
白名单工具调用（SSE 响应路径）、非白名单拒绝与审计、禁用与连接失败的错误路径。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from ai.models.mcp import McpServer
from system.models import OperationLog

pytestmark = pytest.mark.django_db

MCP_URL = "/api/ai/mcp-servers"


class _McpStub(BaseHTTPRequestHandler):
    """进程内 MCP 桩：initialize / notifications/initialized / tools/list / tools/call。"""

    protocol_version = "HTTP/1.1"
    received = []

    TOOLS = [
        {
            "name": "echo",
            "description": "Echo text",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {"name": "danger", "description": "Dangerous op", "inputSchema": {"type": "object", "properties": {}}},
    ]

    def log_message(self, *args):  # 静默访问日志
        pass

    def _send(self, body: bytes, *, content_type: str = "application/json", status: int = 200, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 基类接口名
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(b"{}", status=400)
            return
        _McpStub.received.append({"method": payload.get("method"), "headers": dict(self.headers)})
        method = payload.get("method")
        rpc_id = payload.get("id")
        if method == "notifications/initialized":
            self._send(b"", status=202)
            return
        if method == "initialize":
            body = json.dumps(
                {"jsonrpc": "2.0", "id": rpc_id, "result": {"protocolVersion": "2025-06-18", "capabilities": {}}}
            )
            self._send(body.encode(), extra_headers={"Mcp-Session-Id": "stub-session"})
            return
        if method == "tools/list":
            body = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "result": {"tools": self.TOOLS}})
            self._send(body.encode())
            return
        if method == "tools/call":
            params = payload.get("params") or {}
            if params.get("name") == "echo":
                result = {
                    "content": [
                        {"type": "text", "text": "echo: " + str((params.get("arguments") or {}).get("text", ""))}
                    ]
                }
                # 用 SSE 承载响应，覆盖流式解析路径
                line = "data: " + json.dumps({"jsonrpc": "2.0", "id": rpc_id, "result": result}) + "\n\n"
                self._send(line.encode(), content_type="text/event-stream")
                return
            body = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32000, "message": "unknown tool"}})
            self._send(body.encode())
            return
        body = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "method not found"}})
        self._send(body.encode())


@pytest.fixture
def stub():
    _McpStub.received = []
    server = HTTPServer(("127.0.0.1", 0), _McpStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/mcp", _McpStub
    server.shutdown()


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def client(superuser):
    api = APIClient()
    api.force_authenticate(superuser)
    return api


def _create_server(client, url, **extra):
    payload = {"name": "本机桩", "url": url, "allowed_tools": ["echo"], **extra}
    res = client.post(MCP_URL, payload, format="json")
    assert res.data["code"] == 1000, res.data
    return res.data["data"]["pk"]


class TestServerCrud:
    def test_create_and_token_write_only(self, client, stub):
        url, _ = stub
        pk = _create_server(client, url, auth_header="Authorization", auth_token="tok-1")
        detail = client.get(f"{MCP_URL}/{pk}")
        assert detail.data["code"] == 1000
        data = detail.data["data"]
        assert data["auth_token_set"] is True
        assert "auth_token" not in data
        stored = McpServer.objects.get(pk=pk)
        assert stored.auth_token_plain == "tok-1"
        assert "tok-1" not in stored.auth_token

    def test_reject_intranet_http_url(self, client):
        res = client.post(MCP_URL, {"name": "内网", "url": "http://192.168.1.10:8080/mcp"}, format="json")
        assert res.status_code == 400

    def test_allowed_tools_deduped(self, client, stub):
        url, _ = stub
        pk = _create_server(client, url, allowed_tools=["echo", "echo", " ", "other"])
        stored = McpServer.objects.get(pk=pk)
        assert stored.tool_names == ["echo", "other"]


class TestSync:
    def test_sync_stores_snapshot_and_session(self, client, stub):
        url, receiver = stub
        pk = _create_server(client, url)
        res = client.post(f"{MCP_URL}/{pk}/sync")
        assert res.data["code"] == 1000, res.data
        data = res.data["data"]
        assert data["count"] == 2
        assert [tool["name"] for tool in data["tools"]] == ["echo", "danger"]
        assert data["tools"][0]["read_only"] is True
        server = McpServer.objects.get(pk=pk)
        assert server.last_synced_time is not None
        assert server.last_sync_error == ""
        # 会话头回带：initialize 之后的请求携带 Mcp-Session-Id
        assert any(item["headers"].get("Mcp-Session-Id") == "stub-session" for item in receiver.received)

    def test_sync_failure_records_error(self, client):
        pk = _create_server(client, "http://127.0.0.1:9/mcp")
        res = client.post(f"{MCP_URL}/{pk}/sync")
        assert res.data["code"] == 1001
        server = McpServer.objects.get(pk=pk)
        assert server.last_sync_error

    def test_sync_disabled_server_rejected(self, client, stub):
        url, _ = stub
        pk = _create_server(client, url)
        assert client.patch(f"{MCP_URL}/{pk}", {"enabled": False}, format="json").data["code"] == 1000
        res = client.post(f"{MCP_URL}/{pk}/sync")
        assert res.data["code"] == 1001


class TestCall:
    def test_call_whitelisted_tool_via_sse(self, client, stub):
        url, _ = stub
        pk = _create_server(client, url)
        res = client.post(f"{MCP_URL}/{pk}/call", {"tool": "echo", "arguments": {"text": "hello"}}, format="json")
        assert res.data["code"] == 1000, res.data
        assert res.data["data"]["text"] == "echo: hello"
        assert res.data["data"]["is_error"] is False
        log = OperationLog.objects.filter(module="AI:mcp:client").first()
        assert log is not None
        assert log.status_code == 1000

    def test_call_tool_outside_whitelist_rejected_and_audited(self, client, stub):
        url, _ = stub
        pk = _create_server(client, url)
        res = client.post(f"{MCP_URL}/{pk}/call", {"tool": "danger", "arguments": {}}, format="json")
        assert res.data["code"] == 1001
        log = OperationLog.objects.filter(module="AI:mcp:client").first()
        assert log is not None
        assert log.status_code == 1001

    def test_call_remote_error_recorded(self, client, stub):
        url, _ = stub
        pk = _create_server(client, url, allowed_tools=["echo", "ghost"])
        res = client.post(f"{MCP_URL}/{pk}/call", {"tool": "ghost", "arguments": {}}, format="json")
        assert res.data["code"] == 1001
        assert OperationLog.objects.filter(module="AI:mcp:client", status_code=1001).exists()

    def test_call_requires_tool_name(self, client, stub):
        url, _ = stub
        pk = _create_server(client, url)
        res = client.post(f"{MCP_URL}/{pk}/call", {"arguments": {}}, format="json")
        assert res.data["code"] == 1001

    def test_call_disabled_server_rejected(self, client, stub):
        url, _ = stub
        pk = _create_server(client, url)
        client.patch(f"{MCP_URL}/{pk}", {"enabled": False}, format="json")
        res = client.post(f"{MCP_URL}/{pk}/call", {"tool": "echo", "arguments": {}}, format="json")
        assert res.data["code"] == 1001
