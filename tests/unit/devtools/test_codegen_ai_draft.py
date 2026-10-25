# -*- coding: utf-8 -*-
"""AI 字段草案服务单测：草案解析过滤（与 apply_field_overrides 同套约束）+ 生成链路。

LLM 不真连：tracked_chat / structured_chat_client / 配置闸分别打桩；
解析层是纯函数，直接喂原始 JSON 文本。
"""

import json
from typing import Any

import pytest

from devtools.services.codegen import ai_draft
from devtools.services.codegen.fields import CodegenError

pytest.importorskip("demo")

pytestmark = pytest.mark.django_db

PLAN: dict[str, Any] = {
    "fields": [
        {"name": "pk", "is_relation": False},
        {"name": "name", "is_relation": False},
        {"name": "status", "is_relation": False},
        {"name": "author", "is_relation": True},
    ],
    "dict_types": [{"code": "book-status", "label": "状态"}],
}


def _raw(fields: Any, summary: str = "ok") -> str:
    return json.dumps({"fields": fields, "summary": summary}, ensure_ascii=False)


class TestParseDraft:
    def test_happy_path_keeps_legal_overrides(self):
        result = ai_draft.parse_draft(
            _raw(
                [
                    {"name": "name", "label": "书名", "required": True, "in_search": True},
                    {"name": "author", "input_type": "api-search-user"},
                    {"name": "status", "dict_code": "book-status"},
                ]
            ),
            PLAN,
        )
        assert result["summary"] == "ok"
        assert result["warnings"] == []
        assert result["fields"] == [
            {"name": "name", "label": "书名", "required": True, "in_search": True},
            {"name": "author", "input_type": "api-search-user"},
            {"name": "status", "dict_code": "book-status"},
        ]

    def test_field_class_legal_and_exclusive_with_dict(self):
        result = ai_draft.parse_draft(
            _raw([{"name": "status", "dict_code": "book-status", "field_class": "ColorField"}]),
            PLAN,
        )
        assert result["fields"] == [{"name": "status", "dict_code": "book-status"}]
        assert len(result["warnings"]) == 1

    def test_illegal_items_stripped_to_warnings_not_rejected(self):
        result = ai_draft.parse_draft(
            _raw(
                [
                    "not-an-object",
                    {"name": "ghost"},
                    {"name": "name", "input_type": "input"},  # 非关联字段
                    {"name": "author", "dict_code": "book-status"},  # 关联字段
                    {"name": "status", "dict_code": "no-such-dict"},
                    {"name": "status", "label": "x"},  # 重复（首项已收敛）
                ]
            ),
            PLAN,
        )
        assert result["fields"] == [
            {"name": "name"},
            {"name": "author"},
            {"name": "status"},
        ]
        assert len(result["warnings"]) == 6

    def test_pk_exclude_flag_dropped(self):
        result = ai_draft.parse_draft(_raw([{"name": "pk", "include": False}]), PLAN)
        assert result["fields"] == [{"name": "pk"}]
        assert len(result["warnings"]) == 1

    def test_truncation_over_sixty_four(self):
        big_plan = {
            "fields": [{"name": f"f{i}", "is_relation": False} for i in range(70)],
            "dict_types": [],
        }
        rows = [{"name": f"f{i}"} for i in range(70)]
        result = ai_draft.parse_draft(_raw(rows), big_plan)
        assert len(result["fields"]) == 64
        # 截断告警恰好一条（文案随 locale，断言数量不断言语言）
        assert len(result["warnings"]) == 1

    def test_invalid_json_and_missing_fields_array(self):
        with pytest.raises(CodegenError):
            ai_draft.parse_draft("not json at all {{{", PLAN)
        with pytest.raises(CodegenError):
            ai_draft.parse_draft(json.dumps({"summary": "no fields"}), PLAN)


class TestGenerateAiDraft:
    def test_disabled_by_default(self):
        with pytest.raises(CodegenError):
            ai_draft.generate_ai_draft(None, "demo.Book", "书籍管理页")

    def test_enabled_but_not_configured(self, monkeypatch: pytest.MonkeyPatch):
        from ai.services.assistant import ai_config

        monkeypatch.setattr(ai_draft, "draft_enabled", lambda: True)
        monkeypatch.setattr(ai_config, "is_configured", lambda: False)
        with pytest.raises(CodegenError):
            ai_draft.generate_ai_draft(None, "demo.Book", "书籍管理页")

    def test_generate_full_chain(self, monkeypatch: pytest.MonkeyPatch):
        from ai.models.ai import AiUsageRecord
        from ai.services.assistant import ai_config, ai_usage

        monkeypatch.setattr(ai_draft, "draft_enabled", lambda: True)
        monkeypatch.setattr(ai_config, "is_configured", lambda: True)

        class _Client:
            max_tokens = 256

        monkeypatch.setattr(ai_config, "structured_chat_client", lambda: (_Client(), 256))

        recorded: dict[str, Any] = {}

        def fake_tracked_chat(user: Any, feature: str, messages: list[Any], client: Any = None, **kw: Any) -> str:
            recorded.update(user=user, feature=feature, prompt=messages[1]["content"])
            return _raw([{"name": "name", "label": "书名", "required": True}], summary="草案就绪")

        monkeypatch.setattr(ai_usage, "tracked_chat", fake_tracked_chat)
        result = ai_draft.generate_ai_draft("user-obj", "demo.Book", "做一个书籍管理页，名称必填")
        assert result["model"] == "demo.Book"
        assert result["fields"][0]["label"] == "书名"
        assert recorded["feature"] == AiUsageRecord.Feature.CODEGEN
        assert recorded["user"] == "user-obj"
        # 用户描述进入 prompt；引用目录由 annotate_reference 包裹后注入
        assert "做一个书籍管理页" in recorded["prompt"]
