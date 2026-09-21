# -*- coding: utf-8 -*-
"""LLM 输出 JSON 提取（跨入口公共件）单测。

覆盖动作草稿与 NL 查数共用的解析口径：码栅剥离、前后废话容忍、
非对象/非法 JSON 拒绝（调用方转各自框架错误）。
"""

import pytest

from system.utils.ai_parse import AiOutputParseError, extract_json_object


class TestExtractJsonObject:
    def test_plain_object(self):
        assert extract_json_object('{"action": "user.search"}') == {"action": "user.search"}

    def test_fenced_json_block(self):
        text = '```json\n{"action": "leave.submit", "params": {"days": 2}}\n```'
        assert extract_json_object(text) == {"action": "leave.submit", "params": {"days": 2}}

    def test_fenced_plain_block(self):
        text = '```\n{"a": 1}\n```'
        assert extract_json_object(text) == {"a": 1}

    def test_surrounding_prose(self):
        text = '好的，这是你要的草稿：\n{"a": 1}\n还需要我做什么？'
        assert extract_json_object(text) == {"a": 1}

    def test_array_payload_rejected(self):
        with pytest.raises(AiOutputParseError):
            extract_json_object("[1, 2, 3]")

    def test_malformed_json_rejected(self):
        with pytest.raises(AiOutputParseError):
            extract_json_object('{"a": ')

    def test_empty_text_rejected(self):
        with pytest.raises(AiOutputParseError):
            extract_json_object("")

    def test_none_text_rejected(self):
        with pytest.raises(AiOutputParseError):
            extract_json_object(None)


class TestCallerErrorMapping:
    """调用方错误类型映射：动作草稿与 NL 查数都走 Django ValidationError（DRF 视图转 400）。"""

    def test_action_draft_maps_to_django_validation_error(self):
        from django.core.exceptions import ValidationError as DjangoValidationError

        from system.utils.ai_actions import _extract_json_object

        with pytest.raises(DjangoValidationError):
            _extract_json_object("不是 JSON")

    def test_nl_query_maps_to_django_validation_error(self):
        from django.core.exceptions import ValidationError as DjangoValidationError

        from system.utils.nl_query import parse_llm_json

        with pytest.raises(DjangoValidationError):
            parse_llm_json("不是 JSON")

    def test_nl_query_drops_unknown_keys(self):
        from system.utils.nl_query import parse_llm_json

        payload = parse_llm_json('{"dataset": "x", "mode": "detail", "sql": "drop table"}')
        assert "sql" not in payload  # 未知键剥离（白名单外不带执行语义）
        assert payload["dataset"] == "x"
