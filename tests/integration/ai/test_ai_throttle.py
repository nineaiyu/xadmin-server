# -*- coding: utf-8 -*-
"""AI 端点 DRF 层限流：LLM 调用类 action 按用户限速、只读动作不限、超限 429/999。"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from common.core.throttle import AiAdminThrottle, AiChatThrottle

pytestmark = pytest.mark.django_db

ASSISTANT_URL = "/api/ai/assistant"
CONFIG_URL = "/api/ai/assistant/config"
MCP_URL = "/api/ai/mcp"


@pytest.fixture
def set_rate(monkeypatch):
    """把限流类的速率改成测试值（实例化期 get_rate 读取）。"""

    def _set(throttle_cls, value):
        monkeypatch.setattr(throttle_cls, "get_rate", lambda self: value)

    return _set


class TestAiChatThrottle:
    def test_ask_throttled_after_limit(self, auth_client, set_rate):
        """超限 429（业务码 999）：限流先于业务逻辑执行。"""
        set_rate(AiChatThrottle, "3/m")
        for _ in range(3):
            response = auth_client.post(f"{ASSISTANT_URL}/ask", {"question": "什么是数据集"}, format="json")
            assert response.status_code == status.HTTP_200_OK
        blocked = auth_client.post(f"{ASSISTANT_URL}/ask", {"question": "什么是数据集"}, format="json")
        assert blocked.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert blocked.json()["code"] == 999

    def test_readonly_actions_not_throttled(self, auth_client, set_rate):
        """status 等只读动作不进 ai_chat 计数（1/m 也不影响只读轮询）。"""
        set_rate(AiChatThrottle, "1/m")
        for _ in range(4):
            assert auth_client.get(f"{ASSISTANT_URL}/status").status_code == status.HTTP_200_OK

    def test_mcp_endpoint_throttled(self, auth_client, set_rate):
        """MCP JSON-RPC 端点（tools/list/call 入口）同样按对话类限流。"""
        set_rate(AiChatThrottle, "2/m")
        payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        for _ in range(2):
            assert auth_client.post(MCP_URL, payload, format="json").status_code == status.HTTP_200_OK
        assert auth_client.post(MCP_URL, payload, format="json").status_code == status.HTTP_429_TOO_MANY_REQUESTS

    def test_limit_is_per_user(self, auth_client, set_rate):
        from identity.models import UserInfo

        set_rate(AiChatThrottle, "1/m")
        assert auth_client.post(f"{ASSISTANT_URL}/ask", {}, format="json").status_code == status.HTTP_200_OK
        assert (
            auth_client.post(f"{ASSISTANT_URL}/ask", {}, format="json").status_code == status.HTTP_429_TOO_MANY_REQUESTS
        )
        other = APIClient()
        other.force_authenticate(UserInfo.objects.create_superuser(username="throttle2", password="Admin@123456"))
        # 另一用户独立计数：不受前一用户的限流历史影响
        assert other.post(f"{ASSISTANT_URL}/ask", {}, format="json").status_code == status.HTTP_200_OK


class TestAiAdminThrottle:
    def test_connection_test_uses_admin_scope(self, auth_client, set_rate):
        """连接测试（create）命中 ai_admin：ai_chat 不限也不放行。"""
        set_rate(AiAdminThrottle, "1/m")
        set_rate(AiChatThrottle, "100/m")
        first = auth_client.post(CONFIG_URL, {}, format="json")
        assert first.status_code != status.HTTP_429_TOO_MANY_REQUESTS
        second = auth_client.post(CONFIG_URL, {}, format="json")
        assert second.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    def test_config_read_not_throttled_by_admin_scope(self, auth_client, set_rate):
        """配置读取（retrieve）不是重操作：1/m 下连续读取不受限。"""
        set_rate(AiAdminThrottle, "1/m")
        for _ in range(3):
            assert auth_client.get(CONFIG_URL).status_code == status.HTTP_200_OK
