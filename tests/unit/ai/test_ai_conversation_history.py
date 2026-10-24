# -*- coding: utf-8 -*-
"""助手页多轮上下文：历史读取与 prompt 装配。"""

from types import SimpleNamespace

import pytest

from ai.models.ai import AiChatMessage
from ai.services.assistant import ai as ai_service

pytestmark = pytest.mark.django_db


class TestConversationHistory:
    def test_orders_by_id_and_keeps_recent_limit(self, superuser):
        for index in range(5):
            AiChatMessage.objects.create(creator=superuser, feature="docs", role="user", content=f"q{index}")
            AiChatMessage.objects.create(creator=superuser, feature="docs", role="assistant", content=f"a{index}")

        history = ai_service.conversation_history(superuser, limit=4)

        assert [item["content"] for item in history] == ["q3", "a3", "q4", "a4"]
        assert [item["role"] for item in history] == ["user", "assistant", "user", "assistant"]

    def test_skips_system_errors_and_other_features(self, superuser):
        AiChatMessage.objects.create(creator=superuser, feature="docs", role="user", content="问题")
        AiChatMessage.objects.create(creator=superuser, feature="docs", role="system", content="加载失败")
        AiChatMessage.objects.create(creator=superuser, feature="nl", role="user", content="另一个入口")
        AiChatMessage.objects.create(creator=superuser, feature="docs", role="assistant", content="")

        history = ai_service.conversation_history(superuser)

        assert [item["content"] for item in history] == ["问题"]

    def test_zero_limit_disables_history(self, superuser):
        AiChatMessage.objects.create(creator=superuser, feature="docs", role="user", content="问题")
        assert ai_service.conversation_history(superuser, limit=0) == []


class TestPrepareRagMessages:
    def test_history_sits_between_system_and_current_question(self, monkeypatch):
        chunk = SimpleNamespace(
            content="文档正文",
            title="文档标题",
            source_path="guide/doc.md",
            chunk_index=0,
        )
        monkeypatch.setattr(ai_service, "is_enabled", lambda: True)
        monkeypatch.setattr(ai_service, "retrieve", lambda _question: [{"chunk": chunk}])

        messages, sources, _hits = ai_service._prepare_rag(
            "那它的默认值呢？",
            user=None,
            history=[
                {"role": "user", "content": "这个参数是什么？"},
                {"role": "assistant", "content": "它是超时配置。"},
            ],
        )

        assert [item["role"] for item in messages] == ["system", "user", "assistant", "user"]
        assert messages[1]["content"] == "这个参数是什么？"
        assert messages[2]["content"] == "它是超时配置。"
        assert messages[-1]["content"].endswith("那它的默认值呢？")
        assert sources == [{"title": "文档标题", "path": "guide/doc.md", "chunk_index": 0}]
