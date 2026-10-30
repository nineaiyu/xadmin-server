# -*- coding: utf-8 -*-
"""MCP 治理（提案 P5）集成测试：服务器级日调用配额 + 审计报表端点。

配额：计数源 = 审计流水（OperationLog module=AI:mcp:client 当日行数），
预置审计行驱动计数，不打真实外部服务器（配额在出站前拦截）。
"""

import pytest
from rest_framework.test import APIClient

from ai.models.mcp import McpServer
from ai.services.mcp.mcp_client import audit_mcp_call

pytestmark = pytest.mark.django_db

SERVER_URL = "/api/ai/mcp-servers"
LOG_URL = "/api/ai/mcp-call-logs"


@pytest.fixture
def client(superuser):
    api = APIClient()
    api.force_authenticate(superuser)
    return api


@pytest.fixture
def server(superuser):
    return McpServer.objects.create(
        name="quota-stub",
        url="https://mcp.example.com/mcp",
        allowed_tools=["echo"],
        expose_to_ai=True,
        enabled=True,
    )


def _seed_calls(server, user, count, ok=True):
    for _ in range(count):
        audit_mcp_call(user, server, "echo", ok, "seeded")


class TestDailyQuota:
    def test_zero_limit_unlimited(self, server, superuser):
        from ai.services.mcp.mcp_client import daily_quota_error

        _seed_calls(server, superuser, 5)
        assert daily_quota_error(server) == ""

    def test_quota_counts_audit_rows_of_today(self, server, superuser):
        from ai.services.mcp.mcp_client import daily_quota_error

        server.daily_call_limit = 3
        server.save(update_fields=["daily_call_limit"])
        _seed_calls(server, superuser, 3)
        message = daily_quota_error(server)
        assert "3/3" in message

    def test_quota_not_reached_passes(self, server, superuser):
        from ai.services.mcp.mcp_client import daily_quota_error

        server.daily_call_limit = 3
        server.save(update_fields=["daily_call_limit"])
        _seed_calls(server, superuser, 2)
        assert daily_quota_error(server) == ""

    def test_call_blocked_by_quota_and_audited(self, client, server, superuser):
        server.daily_call_limit = 1
        server.save(update_fields=["daily_call_limit"])
        _seed_calls(server, superuser, 1)
        response = client.post(f"{SERVER_URL}/{server.pk}/call", {"tool": "echo", "arguments": {}}, format="json")
        assert response.data["code"] == 1001
        assert "1/1" in response.data["detail"]
        # 拒绝行也落审计（计入当日用量）
        from audit.models import OperationLog

        latest = OperationLog.objects.filter(module="AI:mcp:client").order_by("-created_time").first()
        assert "quota" in (latest.changes or "") or "配额" in (latest.changes or "")

    def test_validate_daily_call_limit_bounds(self, client):
        # URL 只须过写入侧守卫，无需真实出站（配额校验在创建/更新侧即触发）
        res = client.post(
            SERVER_URL,
            {"name": "limit-bad", "url": "https://mcp.example.com/mcp", "daily_call_limit": -1},
            format="json",
        )
        # DRF 字段校验失败 = HTTP 400（与 timeout 非数字同口径）
        assert res.status_code == 400, res.data
        res = client.post(
            SERVER_URL,
            {"name": "limit-ok", "url": "https://mcp.example.com/mcp", "daily_call_limit": 500},
            format="json",
        )
        assert res.data["code"] == 1000, res.data


class TestMcpCallLogReport:
    def test_list_scoped_to_mcp_module(self, client, server, superuser):
        _seed_calls(server, superuser, 2)
        # 干扰行：其他 module 的审计不进报表
        from audit.models import OperationLog

        OperationLog.objects.create(module="AI:action", object_pk=str(server.pk), status_code=1000)
        response = client.get(LOG_URL)
        rows = response.data["data"]["results"]
        assert len(rows) == 2
        assert all(row["server_name"] == "quota-stub" for row in rows)
        assert all(row["tool"] == "echo" for row in rows)

    def test_filter_by_server_and_ok(self, client, server, superuser):
        audit_mcp_call(superuser, server, "echo", True, "fine")
        audit_mcp_call(superuser, server, "echo", False, "boom")
        failed = client.get(LOG_URL, {"object_pk": server.pk, "ok": "false"}).data["data"]["results"]
        assert len(failed) == 1
        assert failed[0]["detail"] == "boom"
        assert failed[0]["ok"] is False

    def test_channel_extraction(self, client, server, superuser):
        audit_mcp_call(superuser, server, "echo", True, "via ai", extra={"channel": "mcp_tool"})
        audit_mcp_call(superuser, server, "echo", True, "via admin")
        rows = client.get(LOG_URL).data["data"]["results"]
        channels = sorted(row["channel"] for row in rows)
        assert channels == ["", "mcp_tool"]

    def test_no_delete_endpoint(self, client, server):
        assert client.delete(f"{LOG_URL}/nonexistent").status_code == 405

    def test_regular_user_without_permission_rejected(self, normal_user, server):
        from rest_framework.test import APIClient

        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        assert client.get(LOG_URL).status_code == 403
