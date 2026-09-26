# -*- coding: utf-8 -*-
"""AI 助手对话持久化（AiChatMessage）与统一工具目录集成测试。

覆盖：ask / ask_stream 的消息落库与载荷回传（meta/done/error 携带服务端
载荷）、history 端点（入口隔离 / 时间正序 / before_id 翻页 / 用户隔离）、
persist_message 的 JSON 安全（NL 结果行含 UUID）、tools 目录（MCP 风格
inputSchema；灰度关闭为空）。
"""

import json
import uuid

import pytest
from rest_framework.test import APIClient

from ai.models.ai import AiChatMessage

pytestmark = pytest.mark.django_db

ASSISTANT_URL = "/api/system/ai/assistant"
KNOWLEDGE_CONTENT = "数据集是绑定白名单模型的受控查询，执行时按调用者数据权限过滤。"


def _iter_stream(response):
    content = response.streaming_content
    if getattr(response, "is_async", False):
        from asgiref.sync import async_to_sync

        async def gather():
            return [chunk async for chunk in content]

        return async_to_sync(gather)()
    return content


def _parse_sse(response):
    frames = []
    buffer = b""
    for chunk in _iter_stream(response):
        buffer += chunk if isinstance(chunk, bytes) else str(chunk).encode()
        while b"\n\n" in buffer:
            raw, buffer = buffer.split(b"\n\n", 1)
            event, data = "message", ""
            for line in raw.decode().splitlines():
                if line.startswith("event:"):
                    event = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data = line[len("data:") :].strip()
            frames.append((event, json.loads(data) if data else {}))
    return frames


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class StubLLM:
    """记录请求的假 LLM：固定回答。"""

    def __init__(self, answer="根据 [1] 的说明，数据集执行时会按数据权限过滤。"):
        self.answer = answer
        self.requests = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.requests.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse({"choices": [{"message": {"content": self.answer}}]})


@pytest.fixture
def stub_llm(monkeypatch):
    stub = StubLLM()
    monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient._client", lambda self: stub)
    return stub


@pytest.fixture
def ai_enabled(settings):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_BASE_URL = "https://ai.example.com/v1"
    settings.AI_API_KEY = "sk-test"
    settings.AI_MODEL = "test-model"
    return settings


@pytest.fixture
def knowledge(db):
    from ai.models.ai import AiKnowledgeChunk

    AiKnowledgeChunk.objects.create(
        source_path="docs/test-knowledge.md",
        title="测试知识文档",
        chunk_index=0,
        content=KNOWLEDGE_CONTENT,
        content_hash="a" * 64,
    )


class TestAskPersist:
    def test_ask_persists_user_and_assistant(self, ai_enabled, knowledge, stub_llm, auth_client):
        response = auth_client.post(f"{ASSISTANT_URL}/ask", {"question": "数据集如何过滤"}, format="json")
        assert response.status_code == 200
        body = response.json()["data"]
        rows = list(AiChatMessage.objects.filter(feature="docs").order_by("id"))
        assert len(rows) == 2
        assert rows[0].role == "user" and rows[0].content == "数据集如何过滤"
        assert rows[1].role == "assistant" and "数据权限" in rows[1].content
        assert rows[1].extra.get("sources")
        # 响应携带持久化载荷（前端据以上屏，本地不另造展示格式）
        assert body["message"]["id"] == rows[1].pk
        assert body["message"]["reasoning"] == ""

    def test_ask_failure_persists_user_and_error(self, knowledge, auth_client, settings):
        settings.AI_ASSISTANT_ENABLED = False
        response = auth_client.post(f"{ASSISTANT_URL}/ask", {"question": "什么是数据集"}, format="json")
        assert response.json()["code"] == 1001
        roles = list(AiChatMessage.objects.filter(feature="docs").order_by("id").values_list("role", flat=True))
        assert roles == ["user", "system"]
        assert AiChatMessage.objects.filter(role="system", extra__error=True).exists()


class TestAskStreamPersist:
    def test_stream_persists_with_reasoning(self, ai_enabled, knowledge, auth_client, monkeypatch):
        def fake_stream(self, messages, **kwargs):
            yield {"type": "reasoning", "text": "先检索文档"}
            yield {"type": "content", "text": "答案内容"}

        monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient.chat_stream", fake_stream)
        response = auth_client.post(f"{ASSISTANT_URL}/ask/stream", {"question": "数据集如何过滤"}, format="json")
        frames = _parse_sse(response)
        meta, done = frames[0][1], frames[-1][1]
        # meta 带回持久化后的用户消息（乐观上屏对齐）
        assert meta["user_message"]["role"] == "user"
        assert meta["user_message"]["content"] == "数据集如何过滤"
        assert done["message"]["role"] == "assistant"
        assert done["message"]["reasoning"] == "先检索文档"
        rows = list(AiChatMessage.objects.filter(feature="docs").order_by("id"))
        assert len(rows) == 2
        assert rows[1].reasoning == "先检索文档"
        assert rows[1].extra.get("sources")

    def test_gate_error_persists_nothing(self, knowledge, auth_client, settings):
        """响应头前门禁失败：不落任何消息（本轮提问未进入对话流）。"""
        settings.AI_ASSISTANT_ENABLED = False
        auth_client.post(f"{ASSISTANT_URL}/ask/stream", {"question": "x"}, format="json")
        assert AiChatMessage.objects.count() == 0

    def test_only_reasoning_error_persists_thinking(self, ai_enabled, knowledge, auth_client, monkeypatch):
        """只有思考没有回答：保留思考（assistant + partial 标记），前端可回看。"""
        from django.utils.translation import gettext as _t

        def fake_stream(self, messages, **kwargs):
            yield {"type": "reasoning", "text": "想了很久没结论"}

        monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient.chat_stream", fake_stream)
        frames = _parse_sse(
            auth_client.post(f"{ASSISTANT_URL}/ask/stream", {"question": "数据集如何过滤"}, format="json")
        )
        assert frames[-1][0] == "error"
        assert frames[-1][1]["message"]["extra"]["partial"]
        row = AiChatMessage.objects.get(role="assistant")
        assert row.reasoning == "想了很久没结论"
        assert frames[-1][1]["detail"] == _t("The model did not provide a final answer; please retry or switch models")


class TestHistory:
    @staticmethod
    def _seed(user, feature: str, rounds: int):
        for index in range(rounds):
            AiChatMessage.objects.create(feature=feature, role="user", content=f"q-{feature}-{index}", creator=user)
            AiChatMessage.objects.create(
                feature=feature, role="assistant", content=f"a-{feature}-{index}", creator=user
            )

    def test_feature_isolated_and_ordered(self, superuser, auth_client):
        self._seed(superuser, "docs", 2)
        self._seed(superuser, "nl", 1)
        body = auth_client.get(f"{ASSISTANT_URL}/history", {"feature": "docs"}).json()["data"]
        assert body["has_more"] is False
        assert [row["content"] for row in body["results"]] == ["q-docs-0", "a-docs-0", "q-docs-1", "a-docs-1"]

    def test_pagination_with_before_id(self, superuser, auth_client):
        self._seed(superuser, "docs", 3)
        first = auth_client.get(f"{ASSISTANT_URL}/history", {"feature": "docs", "limit": 2}).json()["data"]
        assert first["has_more"] is True
        assert len(first["results"]) == 2
        before_id = first["results"][0]["id"]
        second = auth_client.get(
            f"{ASSISTANT_URL}/history", {"feature": "docs", "limit": 2, "before_id": before_id}
        ).json()["data"]
        assert second["results"][0]["id"] != before_id

    def test_user_isolated(self, superuser, normal_user, auth_client):
        self._seed(superuser, "docs", 1)
        self._seed(normal_user, "docs", 1)
        from system.models import Menu, MenuMeta

        meta = MenuMeta.objects.create(title="status:AiAssistant")
        menu = Menu.objects.create(
            name="status:AiAssistant",
            path=r"api/system/ai/assistant/(status|metrics|history|tools)$",
            method="GET",
            menu_type=Menu.MenuChoices.PERMISSION,
            meta=meta,
        )
        normal_user.roles.first().menu.set([menu])
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        body = client.get(f"{ASSISTANT_URL}/history", {"feature": "docs"}).json()["data"]
        assert [row["content"] for row in body["results"]] == ["q-docs-0", "a-docs-0"]

    def test_invalid_feature_falls_back(self, superuser, auth_client):
        self._seed(superuser, "docs", 1)
        body = auth_client.get(f"{ASSISTANT_URL}/history", {"feature": "hacker"}).json()["data"]
        assert len(body["results"]) == 2

    def test_anonymous_rejected(self, api_client):
        assert api_client.get(f"{ASSISTANT_URL}/history", {"feature": "docs"}).status_code == 401


class TestPersistJsonSafe:
    def test_uuid_and_datetime_extra(self, superuser):
        """NL 结果行含 UUID/datetime（values 原样）时落库不炸且可读。"""
        from datetime import datetime

        from ai.utils.ai_chat import persist_message

        row = persist_message(
            superuser,
            "nl",
            "assistant",
            content="查询完成",
            extra={"nl_run": {"rows": [{"dept": uuid.uuid4(), "created": datetime(2026, 9, 20, 12, 0, 0)}]}},
        )
        assert row is not None
        assert isinstance(row.extra["nl_run"]["rows"][0]["dept"], str)


class TestToolsCatalog:
    def test_tools_schema_for_superuser(self, superuser, auth_client, settings):
        settings.AI_ACTION_ENABLED = True
        body = auth_client.get(f"{ASSISTANT_URL}/tools").json()["data"]
        assert body["action_enabled"] is True
        names = [item["name"] for item in body["tools"]]
        assert "leave.submit" in names and "notice.publish" in names and "user.search" in names
        for item in body["tools"]:
            assert item["inputSchema"]["type"] == "object"
            assert set(item["inputSchema"]["required"]).issubset(set(item["inputSchema"]["properties"]))
        # user.search：读类动作参数走 query（inputSchema 有 optional 关键字）
        search = next(item for item in body["tools"] if item["name"] == "user.search")
        assert search["inputSchema"]["required"] == []
        # const 固定参数不下发（服务端持有）
        notice_list = next(item for item in body["tools"] if item["name"] == "notice.list")
        assert "notice_type" not in notice_list["inputSchema"]["properties"]

    def test_tools_disabled_empty(self, auth_client, settings):
        settings.AI_ACTION_ENABLED = False
        body = auth_client.get(f"{ASSISTANT_URL}/tools").json()["data"]
        assert body["action_enabled"] is False
        assert body["tools"] == []
