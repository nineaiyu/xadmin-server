# -*- coding: utf-8 -*-
"""AI 使用/二开助手集成测试。

覆盖：知识库同步（分块/hash 幂等/清理）、检索评分（相关块优先）、ask 全链路
（stub LLM → 引用出处）、降级（未启用/未配置/无命中）、越权与密钥不回显、
配置测试动作。
"""

import json

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from ai.models.ai import AiKnowledgeChunk
from system.models import ModelLabelField

pytestmark = pytest.mark.django_db


def _iter_stream(response):
    """流式响应的字节块（测试用同步收集）。

    ASGI 实时性由 response.is_async 守护测试覆盖；此处仅需完整消费帧序列，
    异步迭代器统一经 async_to_sync 收集（同步生成器直接返回）。
    """
    content = response.streaming_content
    if getattr(response, "is_async", False):
        from asgiref.sync import async_to_sync

        async def gather():
            return [chunk async for chunk in content]

        return async_to_sync(gather)()
    return content


CONFIG_URL = "/api/system/ai/assistant/config"
ASSISTANT_URL = "/api/system/ai/assistant"
KNOWLEDGE_DOC = """# 测试知识文档

## 数据集介绍

数据集是绑定白名单模型的受控查询，执行时按调用者数据权限过滤。

## 仪表盘

仪表盘由卡片组成，卡片引用数据集并选择图表类型。
"""


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def ai_enabled(settings):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_BASE_URL = "https://ai.example.com/v1"
    settings.AI_API_KEY = "sk-test"
    settings.AI_MODEL = "test-model"
    return settings


@pytest.fixture
def knowledge(db):
    root, _ = ModelLabelField.objects.get_or_create(
        name="system.userinfo", defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "用户"}
    )
    for name in ("username",):
        ModelLabelField.objects.get_or_create(
            name=name, parent=root, defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": name}
        )
    # 直接落两个知识块（不依赖仓库 docs 变化）
    AiKnowledgeChunk.objects.create(
        source_path="docs/test-knowledge.md",
        title="测试知识文档",
        chunk_index=0,
        content="数据集是绑定白名单模型的受控查询，执行时按调用者数据权限过滤。",
        content_hash="a" * 64,
    )
    AiKnowledgeChunk.objects.create(
        source_path="docs/test-knowledge.md",
        title="测试知识文档",
        chunk_index=1,
        content="仪表盘由卡片组成，卡片引用数据集并选择图表类型。",
        content_hash="b" * 64,
    )


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


class TestKnowledgeSync:
    def test_sync_creates_chunks(self, tmp_path, monkeypatch, settings):
        from ai.models.ai import AiKnowledgeDocument
        from ai.utils import ai as ai_utils

        doc = tmp_path / "docs"
        doc.mkdir()
        (doc / "demo.md").write_text("# Demo\n\n## Alpha\n\n内容甲\n\n## Beta\n\n内容乙", encoding="utf-8")
        monkeypatch.setattr(ai_utils, "DOCS_DIR", doc)
        monkeypatch.setattr(ai_utils, "ROOT_DOCS", [])

        summary = ai_utils.sync_knowledge()
        # created 为文档级计数（同步登记文档实体 + 重建分块）
        assert summary["created"] == 1
        document = AiKnowledgeDocument.objects.get(path="docs/demo.md")
        assert document.chunk_count == 3
        chunks = AiKnowledgeChunk.objects.filter(source_path__endswith="demo.md").order_by("chunk_index")
        assert chunks[1].title == "Demo"
        assert "内容甲" in chunks[1].content

        # 幂等：重复同步不新增
        again = ai_utils.sync_knowledge()
        assert again["created"] == 0 and again["total"] == 3

    def test_sync_removes_stale(self, tmp_path, monkeypatch):
        from ai.utils import ai as ai_utils

        AiKnowledgeChunk.objects.create(source_path="docs/gone.md", chunk_index=0, content="旧", content_hash="x" * 64)
        doc = tmp_path / "docs"
        doc.mkdir()
        monkeypatch.setattr(ai_utils, "DOCS_DIR", doc)
        monkeypatch.setattr(ai_utils, "ROOT_DOCS", [])
        summary = ai_utils.sync_knowledge()
        assert summary["removed"] == 1
        assert not AiKnowledgeChunk.objects.filter(source_path="docs/gone.md").exists()


class TestRetrieve:
    def test_relevant_chunk_first(self, knowledge):
        from ai.utils.ai import retrieve

        results = retrieve("数据集怎么过滤数据？")
        assert results
        assert "数据集" in results[0]["chunk"].content

    def test_no_match_empty(self, knowledge):
        from ai.utils.ai import retrieve

        assert retrieve("完全无关的量子力学问题") == []


class TestAsk:
    def test_full_chain_with_citations(self, ai_enabled, knowledge, stub_llm, auth_client):
        response = auth_client.post(f"{ASSISTANT_URL}/ask", {"question": "数据集如何做数据权限过滤？"}, format="json")
        assert response.status_code == 200, response.data
        body = response.json()["data"]
        assert "数据权限" in body["answer"]
        assert body["sources"] and body["sources"][0]["path"] == "docs/test-knowledge.md"
        # LLM 请求体：system 约束 + 检索上下文 + 问题
        messages = stub_llm.requests[0]["json"]["messages"]
        assert messages[0]["role"] == "system"
        assert "[1]" in messages[1]["content"]
        assert "数据权限过滤" in messages[1]["content"]

    def test_disabled_rejected(self, knowledge, auth_client, settings):
        settings.AI_ASSISTANT_ENABLED = False
        response = auth_client.post(f"{ASSISTANT_URL}/ask", {"question": "什么是数据集"}, format="json")
        assert response.json()["code"] == 1001

    def test_llm_failure_readable(self, ai_enabled, knowledge, auth_client, monkeypatch):
        """LLM 失败 → 归一为可读文案（不透传内部英文错误；思考型模型单独提示）。"""
        from django.utils.translation import gettext

        from common.sdk.ai.chat import AiSdkError

        monkeypatch.setattr(
            "common.sdk.ai.chat.ChatCompletionsClient.chat",
            lambda self, messages, temperature=0.2: (_ for _ in ()).throw(AiSdkError("provider down")),
        )
        response = auth_client.post(f"{ASSISTANT_URL}/ask", {"question": "数据集如何过滤"}, format="json")
        assert response.json()["code"] == 1001
        assert response.json()["detail"] == str(gettext("AI service is temporarily unavailable"))

    def test_llm_empty_answer_readable(self, ai_enabled, knowledge, auth_client, monkeypatch):
        """空回答（思考过长截断）→ 可读文案给出重试/换模型建议。"""
        from django.utils.translation import gettext

        from common.sdk.ai.chat import AiSdkError

        monkeypatch.setattr(
            "common.sdk.ai.chat.ChatCompletionsClient.chat",
            lambda self, messages, temperature=0.2: (_ for _ in ()).throw(
                AiSdkError("The AI provider returned an empty answer")
            ),
        )
        response = auth_client.post(f"{ASSISTANT_URL}/ask", {"question": "数据集如何过滤"}, format="json")
        assert response.json()["code"] == 1001
        assert response.json()["detail"] == str(
            gettext("The model returned an empty answer; please retry or switch models")
        )

    def test_no_match_readable(self, ai_enabled, knowledge, auth_client):
        response = auth_client.post(f"{ASSISTANT_URL}/ask", {"question": "量子力学"}, format="json")
        assert response.json()["code"] == 1001

    def test_normal_user_allowed_with_menu(self, normal_user, knowledge, ai_enabled, stub_llm):
        """权限门控：授予菜单权限点的普通用户可提问。"""
        from system.models import Menu, MenuMeta

        meta = MenuMeta.objects.create(title="ask:AiAssistant")
        menu = Menu.objects.create(
            name="ask:AiAssistant",
            path="api/system/ai/assistant/ask$",
            method="POST",
            menu_type=Menu.MenuChoices.PERMISSION,
            meta=meta,
        )
        normal_user.roles.first().menu.set([menu])
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        response = client.post(f"{ASSISTANT_URL}/ask", {"question": "数据集如何过滤"}, format="json")
        assert response.status_code == 200

    def test_normal_user_without_menu_rejected(self, normal_user, knowledge, ai_enabled, stub_llm):
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        assert client.post(f"{ASSISTANT_URL}/ask", {"question": "x"}, format="json").status_code == 403


class TestConfigApi:
    def test_anonymous_rejected(self, api_client):
        assert api_client.get(CONFIG_URL).status_code == 401

    def test_retrieve_masks_key(self, auth_client):
        body = auth_client.get(CONFIG_URL).json()["data"]
        assert "AI_API_KEY" not in body
        assert body["AI_ASSISTANT_ENABLED"] is False

    def test_partial_update_encrypts_key(self, auth_client):
        response = auth_client.patch(
            CONFIG_URL,
            {
                "AI_ASSISTANT_ENABLED": True,
                "AI_BASE_URL": "https://ai.example.com/v1",
                "AI_API_KEY": "sk-super-secret",
                "AI_MODEL": "deepseek-chat",
            },
            format="json",
        )
        assert response.status_code == 200, response.data
        from settings.models import Setting

        row = Setting.objects.get(name="AI_API_KEY")
        assert row.encrypted is True
        assert "sk-super-secret" not in (row.value or "")

    def test_status_action(self, auth_client, knowledge, ai_enabled):
        body = auth_client.get(f"{ASSISTANT_URL}/status").json()["data"]
        assert body["enabled"] is True and body["configured"] is True
        assert body["chunks"] == 2

    def test_connection_test(self, auth_client, stub_llm):
        response = auth_client.post(
            CONFIG_URL,
            {"AI_BASE_URL": "https://ai.example.com/v1", "AI_API_KEY": "sk", "AI_MODEL": "m"},
            format="json",
        )
        assert response.status_code == 200, response.data
        # detail = 翻译前缀 + LLM 回复（stub 固定答案）
        assert "数据权限过滤" in response.json()["detail"]

    def test_connection_test_missing_url(self, auth_client):
        response = auth_client.post(CONFIG_URL, {"AI_API_KEY": "sk"}, format="json")
        assert response.json()["code"] == 1001

    @staticmethod
    def _activate_profile(base_url="https://profile.example.com/v1", model="profile-model"):
        from ai.models.ai import AiProfile
        from ai.utils.ai import set_active_profile

        profile = AiProfile.objects.create(name="激活档案", base_url=base_url, model=model)
        profile.api_key_plain = "sk-profile"
        profile.save()
        set_active_profile(profile, True)
        return profile

    def test_connection_test_prefers_form_over_active_profile(self, auth_client, stub_llm):
        """表单值优先于激活档案（回归）：旧实现走 ai_credentials()（档案优先），
        已有激活档案时表单填的值被整体忽略——填了错误地址点测试仍报"连通正常"。"""
        self._activate_profile()
        response = auth_client.post(
            CONFIG_URL,
            {"AI_BASE_URL": "https://form.example.com/v1", "AI_API_KEY": "sk-form", "AI_MODEL": "form-model"},
            format="json",
        )
        assert response.status_code == 200, response.data
        sent = stub_llm.requests[0]
        assert sent["url"] == "https://form.example.com/v1/chat/completions"
        assert sent["json"]["model"] == "form-model"
        assert sent["headers"]["Authorization"] == "Bearer sk-form"

    def test_connection_test_falls_back_to_active_profile(self, auth_client, stub_llm):
        """表单缺省项按激活档案兜底：只填模型时，地址/密钥用档案的（部分填写可测）。"""
        self._activate_profile()
        response = auth_client.post(CONFIG_URL, {"AI_MODEL": "form-model"}, format="json")
        assert response.status_code == 200, response.data
        sent = stub_llm.requests[0]
        assert sent["url"] == "https://profile.example.com/v1/chat/completions"
        assert sent["json"]["model"] == "form-model"
        assert sent["headers"]["Authorization"] == "Bearer sk-profile"


class TestAskStream:
    """文档问答流式（SSE）：meta → reasoning* → delta* → done | error。

    增量解析走真实的 ChatCompletionsClient.chat_stream 消费路径（monkeypatch
    chat_stream 产出事件；SSE 帧解析本身由 test_chat_stream.py 覆盖）。
    """

    STREAM_URL = f"{ASSISTANT_URL}/ask/stream"

    @staticmethod
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

    def test_disabled_gate_returns_json(self, knowledge, auth_client, settings):
        """头前门禁失败仍走 JSON 1001（前端按普通接口错误提示）。"""
        settings.AI_ASSISTANT_ENABLED = False
        response = auth_client.post(self.STREAM_URL, {"question": "什么是数据集"}, format="json")
        assert response.status_code == 200
        assert response.json()["code"] == 1001

    def test_event_order_with_reasoning_and_sources(self, ai_enabled, knowledge, auth_client, monkeypatch):
        """事件序 meta → reasoning → delta* → done；done 携带 answer 与出处；写审计。"""

        def fake_stream(self, messages, **kwargs):
            yield {"type": "reasoning", "text": "先检索文档"}
            yield {"type": "content", "text": "根据 [1]"}
            yield {"type": "content", "text": " 的说明。"}

        monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient.chat_stream", fake_stream)
        response = auth_client.post(self.STREAM_URL, {"question": "数据集如何过滤"}, format="json")
        assert response["Content-Type"] == "text/event-stream"
        frames = self._parse_sse(response)
        assert [event for event, __ in frames] == ["meta", "reasoning", "delta", "delta", "done"]
        assert frames[1][1]["delta"] == "先检索文档"
        done = frames[-1][1]
        assert done["answer"] == "根据 [1] 的说明。"
        assert done["sources"] and done["sources"][0]["path"] == "docs/test-knowledge.md"

        from system.models import OperationLog

        assert OperationLog.objects.filter(module="AI:ask").exists()

    def test_only_reasoning_error_event(self, ai_enabled, knowledge, auth_client, monkeypatch):
        """只有思考没有回答：error 事件带可读文案（思考已单独上屏，前端保留面板）。"""
        from django.utils.translation import gettext as _t

        def fake_stream(self, messages, **kwargs):
            yield {"type": "reasoning", "text": "想了很久没结论"}

        monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient.chat_stream", fake_stream)
        frames = self._parse_sse(auth_client.post(self.STREAM_URL, {"question": "数据集如何过滤"}, format="json"))
        assert [event for event, __ in frames] == ["meta", "reasoning", "error"]
        assert frames[-1][1]["detail"] == _t("The model did not provide a final answer; please retry or switch models")

    def test_accept_header_negotiation(self, ai_enabled, knowledge, auth_client, monkeypatch):
        """浏览器 fetch（Accept: text/event-stream）不得 406（ViewSet 按 action 覆写渲染器）。"""

        def fake_stream(self, messages, **kwargs):
            yield {"type": "content", "text": "答案"}

        monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient.chat_stream", fake_stream)
        response = auth_client.post(
            self.STREAM_URL, {"question": "数据集如何过滤"}, format="json", HTTP_ACCEPT="text/event-stream"
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "text/event-stream"
        assert self._parse_sse(response)[-1][0] == "done"

    def test_stream_uses_async_iterator(self, ai_enabled, knowledge, auth_client, monkeypatch):
        """ASGI 实时性守护：streaming_content 必须是异步迭代器（同步生成器会被 Django 攒住）。"""

        def fake_stream(self, messages, **kwargs):
            yield {"type": "content", "text": "答案"}

        monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient.chat_stream", fake_stream)
        response = auth_client.post(self.STREAM_URL, {"question": "数据集如何过滤"}, format="json")
        assert response.is_async is True
