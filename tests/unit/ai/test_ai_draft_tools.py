# -*- coding: utf-8 -*-
"""原生 function calling 双轨单测：工具定义转换 + tool_calls → 草稿映射。

覆盖：同一份目录三种消费的形态一致性（openai_tools 由 tool_catalog 推导）、
_summary 摘要参数注入与回填、未知动作 / 畸形 JSON / 超量调用 / 空调用的可读拒绝。
"""

import json

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError

from ai.services.actions.ai_actions import ACTION_DASHBOARD_OVERVIEW, drafts_from_tool_calls
from ai.services.actions.ai_draft_tools import draft_result_from_chat
from ai.services.actions.ai_tool_catalog import SUMMARY_PARAM, openai_tools, tool_catalog

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


class TestDraftResultFromChat:
    """原生调用结果分流：tool_calls → 草稿；仅 content → 澄清消息（与 prompt 轨道同构）。

    提示词约定「不可执行时用追问回复」，模型照做时不能被当作解析失败——
    这是「配置了 AI 反而提示模型没有返回可执行请求」的根因护栏。
    """

    def test_tool_calls_map_to_draft(self, superuser):
        result = draft_result_from_chat(
            {"content": "", "tool_calls": [_call(ACTION_DASHBOARD_OVERVIEW, {}, "看看总览")]},
            superuser,
        )
        assert result["kind"] == "draft"
        assert result["draft"]["action"] == ACTION_DASHBOARD_OVERVIEW

    def test_content_only_returns_clarification_message(self, superuser):
        result = draft_result_from_chat(
            {"content": "创建请假申请缺少必填的请假原因（reason），请补充。", "tool_calls": []},
            superuser,
        )
        assert result["kind"] == "message"
        assert "请假原因" in result["message"]

    def test_prompt_json_content_parsed_as_draft(self, superuser):
        """混合模型：未发起工具调用，但按 prompt-JSON 契约输出动作 → 仍解析为草稿。"""
        content = json.dumps(
            {"action": ACTION_DASHBOARD_OVERVIEW, "params": {}, "summary": "看看总览"},
            ensure_ascii=False,
        )
        result = draft_result_from_chat({"content": content, "tool_calls": []}, superuser)
        assert result["kind"] == "draft"
        assert result["draft"]["action"] == ACTION_DASHBOARD_OVERVIEW

    def test_prompt_json_message_content_parsed_as_message(self, superuser):
        content = json.dumps({"message": "请补充请假原因"}, ensure_ascii=False)
        result = draft_result_from_chat({"content": content, "tool_calls": []}, superuser)
        assert result["kind"] == "message"
        assert result["message"] == "请补充请假原因"

    def test_non_contract_json_falls_back_to_message(self, superuser):
        result = draft_result_from_chat({"content": json.dumps({"foo": "bar"}), "tool_calls": []}, superuser)
        assert result["kind"] == "message"
        assert "foo" in result["message"]

    def test_contract_json_with_unknown_action_raises(self, superuser):
        """契约内 JSON 的非法动作按可读校验错误上抛（不伪装成普通气泡）。"""
        content = json.dumps({"action": "no.such.action", "params": {}})
        with pytest.raises(DjangoValidationError):
            draft_result_from_chat({"content": content, "tool_calls": []}, superuser)

    def test_content_is_truncated_to_reason_limit(self, superuser):
        result = draft_result_from_chat({"content": "追问" * 1000, "tool_calls": None}, superuser)
        assert result["kind"] == "message"
        assert len(result["message"]) == 500

    def test_no_tool_calls_and_no_content_rejected(self, superuser):
        with pytest.raises(DjangoValidationError):
            draft_result_from_chat({"content": "", "tool_calls": []}, superuser)
        with pytest.raises(DjangoValidationError):
            draft_result_from_chat({"content": "   ", "tool_calls": []}, superuser)
