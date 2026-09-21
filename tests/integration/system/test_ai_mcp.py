# -*- coding: utf-8 -*-
"""MCP 协议端点集成测试：initialize / tools/list / tools/call / 门禁 / 审计。

覆盖：
- 认证：未认证请求拒绝（与全站 DRF 认证链同口径）；
- initialize：协议版本协商（支持版本回显，未知版本回落默认）；
- tools/list：目录与注册表同源（权限双门过滤 + annotations/_meta 提示）；
- tools/call：读动作执行 + 审计落库；未知工具 / requires_approval 动作 isError；
- 协议错误：未知 method（-32601）、batch 请求（-32600）、通知（202 空体）。
"""

import json

import pytest

from system.models import OperationLog

pytestmark = pytest.mark.django_db

MCP_URL = "/api/system/ai/mcp"


@pytest.fixture
def ai_action_settings(settings):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_BASE_URL = "https://ai.example.com/v1"
    settings.AI_API_KEY = "sk-test"
    settings.AI_MODEL = "test-model"
    settings.AI_ACTION_ENABLED = True
    return settings


def _rpc(method, params=None, msg_id=1):
    payload = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        payload["id"] = msg_id
    if params is not None:
        payload["params"] = params
    return payload


class TestProtocol:
    def test_unauthenticated_rejected(self, db, ai_action_settings, api_client):
        response = api_client.post(MCP_URL, _rpc("initialize"), format="json")
        assert response.status_code in (401, 403)

    def test_initialize(self, auth_client, ai_action_settings):
        response = auth_client.post(MCP_URL, _rpc("initialize", {"protocolVersion": "2025-03-26"}), format="json")
        body = response.json()
        assert body["jsonrpc"] == "2.0"
        assert body["id"] == 1
        assert body["result"]["protocolVersion"] == "2025-03-26"
        assert "tools" in body["result"]["capabilities"]
        assert body["result"]["serverInfo"]["name"] == "xadmin"

    def test_initialize_unknown_version_falls_back(self, auth_client, ai_action_settings):
        response = auth_client.post(MCP_URL, _rpc("initialize", {"protocolVersion": "1999-01-01"}), format="json")
        assert response.json()["result"]["protocolVersion"] == "2025-03-26"

    def test_ping(self, auth_client, ai_action_settings):
        response = auth_client.post(MCP_URL, _rpc("ping"), format="json")
        assert response.json()["result"] == {}

    def test_notification_returns_202(self, auth_client, ai_action_settings):
        response = auth_client.post(MCP_URL, _rpc("notifications/initialized", msg_id=None), format="json")
        assert response.status_code == 202

    def test_unknown_method(self, auth_client, ai_action_settings):
        response = auth_client.post(MCP_URL, _rpc("resources/list"), format="json")
        body = response.json()
        assert body["error"]["code"] == -32601

    def test_batch_request_rejected(self, auth_client, ai_action_settings):
        response = auth_client.post(MCP_URL, [_rpc("ping")], format="json")
        assert response.json()["error"]["code"] == -32600

    def test_gates_when_action_disabled(self, auth_client, settings):
        settings.AI_ASSISTANT_ENABLED = True
        settings.AI_ACTION_ENABLED = False
        response = auth_client.post(MCP_URL, _rpc("tools/list"), format="json")
        assert response.json()["error"]["code"] == -32000


class TestToolsList:
    def test_catalog_matches_registry(self, auth_client, ai_action_settings, superuser):
        from system.utils.ai_actions import available_actions

        response = auth_client.post(MCP_URL, _rpc("tools/list"), format="json")
        tools = response.json()["result"]["tools"]
        by_name = {item["name"]: item for item in tools}
        for spec in available_actions(superuser):
            item = by_name.get(spec.key)
            assert item is not None, f"权限内动作必须出现在 MCP 目录: {spec.key}"
            read_only = all(method.upper() == "GET" for method, __ in spec.required_visits)
            assert item["annotations"]["readOnlyHint"] is read_only
            assert isinstance(item["_meta"]["x-requires-approval"], bool)
            assert "inputSchema" in item and "description" in item


class TestToolsCall:
    def test_read_action_executes_and_audits(self, auth_client, ai_action_settings):
        payload = _rpc("tools/call", {"name": "monitor.overview", "arguments": {}})
        response = auth_client.post(MCP_URL, payload, format="json")
        result = response.json()["result"]
        assert result["isError"] is False
        content = json.loads(result["content"][0]["text"])
        assert content["ok"] is True
        audit = OperationLog.objects.get(module="AI:action")
        assert audit.auth_type == OperationLog.AuthType.AI
        assert json.loads(audit.changes)["channel"] == "mcp"

    def test_unknown_tool_is_error(self, auth_client, ai_action_settings):
        payload = _rpc("tools/call", {"name": "user.destroy_all", "arguments": {}})
        response = auth_client.post(MCP_URL, payload, format="json")
        result = response.json()["result"]
        assert result["isError"] is True

    def test_missing_arguments_rejected(self, auth_client, ai_action_settings):
        """必填参数缺失：服务端逐项校验（客户端输入按不可信处理）。"""
        payload = _rpc("tools/call", {"name": "user.set_active", "arguments": {"pk": "admin"}})
        response = auth_client.post(MCP_URL, payload, format="json")
        result = response.json()["result"]
        assert result["isError"] is True
        assert "is_active" in result["content"][0]["text"]
