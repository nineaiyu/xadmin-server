# -*- coding: utf-8 -*-
"""AI-2 原生 function calling 双轨单测：工具定义转换 + tool_calls → 草稿映射。

覆盖：同一份目录三种消费的形态一致性（openai_tools 由 tool_catalog 推导）、
_summary 摘要参数注入与回填、未知动作 / 畸形 JSON / 超量调用 / 空调用的可读拒绝。
"""

import json

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError

from system.utils.ai_actions import ACTION_DASHBOARD_OVERVIEW, drafts_from_tool_calls
from system.utils.ai_tool_catalog import SUMMARY_PARAM, openai_tools, tool_catalog

pytestmark = pytest.mark.django_db


def _call(name, params, summary="一句话摘要"):
    payload = {**params}
    if summary is not None:
        payload[SUMMARY_PARAM] = summary
    return {"id": "call-1", "name": name, "arguments": json.dumps(payload, ensure_ascii=False)}


class TestOpenAITools:
    def test_same_source_as_mcp_catalog(self, superuser):
        catalog = {entry["name"] for entry in tool_catalog(superuser)}
        tools = openai_tools(superuser)
        assert {tool["function"]["name"] for tool in tools} == catalog
        assert catalog, "目录非空（防权限面为空导致假绿）"

    def test_tool_schema_shape(self, superuser):
        tools = {tool["function"]["name"]: tool for tool in openai_tools(superuser)}
        target = tools[ACTION_DASHBOARD_OVERVIEW]
        assert target["type"] == "function"
        function = target["function"]
        assert function["parameters"]["type"] == "object"
        assert SUMMARY_PARAM in function["parameters"]["properties"]  # 摘要参数（不进业务参数）
        assert len(function["description"]) <= 1024

    def test_unknown_action_rejected(self, superuser):
        with pytest.raises(DjangoValidationError):
            drafts_from_tool_calls(superuser, [_call("no.such.action", {})])

    def test_blank_arguments_treated_as_empty_params(self, superuser):
        result = drafts_from_tool_calls(superuser, [{"id": "a", "name": ACTION_DASHBOARD_OVERVIEW, "arguments": ""}])
        assert result["draft"]["params"] == {}


class TestDraftsFromToolCalls:
    def test_single_call_maps_to_draft(self, superuser):
        result = drafts_from_tool_calls(superuser, [_call(ACTION_DASHBOARD_OVERVIEW, {}, "看看总览")])
        assert result["kind"] == "draft"
        assert result["draft"]["action"] == ACTION_DASHBOARD_OVERVIEW
        assert result["draft"]["summary"] == "看看总览"
        assert result["drafts"] == [result["draft"]]
        assert result["draft"]["requires_approval"] is False

    def test_summary_defaults_to_label(self, superuser):
        result = drafts_from_tool_calls(superuser, [_call(ACTION_DASHBOARD_OVERVIEW, {}, summary=None)])
        assert result["draft"]["summary"]  # 模型未给摘要 → 回落动作标签

    def test_multi_call_order_preserved(self, superuser):
        result = drafts_from_tool_calls(
            superuser,
            [
                {"id": "a", "name": ACTION_DASHBOARD_OVERVIEW, "arguments": "{}"},
                {"id": "b", "name": ACTION_DASHBOARD_OVERVIEW, "arguments": json.dumps({SUMMARY_PARAM: "第二步"})},
            ],
        )
        assert [draft["summary"] for draft in result["drafts"]][0] != "第二步"
        assert result["drafts"][1]["summary"] == "第二步"

    def test_arguments_object_form_accepted(self, superuser):
        result = drafts_from_tool_calls(
            superuser, [{"id": "a", "name": ACTION_DASHBOARD_OVERVIEW, "arguments": {SUMMARY_PARAM: "对象形态"}}]
        )
        assert result["draft"]["summary"] == "对象形态"

    def test_malformed_arguments_rejected(self, superuser):
        with pytest.raises(DjangoValidationError):
            drafts_from_tool_calls(superuser, [{"id": "a", "name": ACTION_DASHBOARD_OVERVIEW, "arguments": "{oops"}])

    def test_too_many_calls_rejected(self, superuser):
        calls = [{"id": str(i), "name": ACTION_DASHBOARD_OVERVIEW, "arguments": "{}"} for i in range(4)]
        with pytest.raises(DjangoValidationError):
            drafts_from_tool_calls(superuser, calls)

    def test_empty_calls_rejected(self, superuser):
        with pytest.raises(DjangoValidationError):
            drafts_from_tool_calls(superuser, [])
        with pytest.raises(DjangoValidationError):
            drafts_from_tool_calls(superuser, [{"id": "a", "name": "", "arguments": "{}"}])
