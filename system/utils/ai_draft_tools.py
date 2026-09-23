#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""AI-2 原生 function calling 双轨：工具调用 → 既有草稿结构（自 ai_actions 拆出，仅行数门禁）。

双轨共用同一下游：本模块只做「工具调用 → drafts」的映射，逐项复用
``ai_actions._build_one_draft`` 的校验链（白名单 / 可用性 / 权限 / 参数规范化），
产出与 ``parse_draft`` 完全同构的结果；执行、审批、审计链路零改动。
"""

import datetime
import json

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _


def build_tool_messages(user, message: str) -> list:
    """原生 tools 轨道的消息：动作目录经 tools 定义下发，不重复进 prompt。

    仍保留引用数据护栏声明（AI-6）：模型/工具定义之外的业务内容不可作为指令。
    """
    from system.utils.ai_actions import MAX_DRAFTS_PER_REQUEST, MAX_MESSAGE_LENGTH
    from system.utils.ai_guard import REFERENCE_GUARD_INSTRUCTION

    system = str(
        _(
            "You convert the user's request into actions for this system by calling the provided tools, "
            "at most {max} calls in execution order (a single call is fine). Use action names exactly as "
            "defined by the tools and only the described parameters. Fill the summary parameter with a "
            "one-line summary. Never invent values the user did not provide; resolve relative dates with "
            "the current date {today}. If the request is not executable or is missing required parameters, "
            "reply with a short clarifying question instead of calling tools."
        )
    )
    system = system.replace("{max}", str(MAX_DRAFTS_PER_REQUEST)).replace("{today}", datetime.date.today().isoformat())
    system = f"{system}\n{REFERENCE_GUARD_INSTRUCTION}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": (message or "").strip()[:MAX_MESSAGE_LENGTH]},
    ]


def _tool_call_params(call: dict) -> tuple:
    """tool_call arguments（JSON 字符串 / 对象）→ (params, summary)；畸形输出抛可读错误。"""
    from system.utils.ai_tool_catalog import SUMMARY_PARAM

    raw = call.get("arguments")
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            params = {}
        else:
            try:
                params = json.loads(raw)
            except ValueError as exc:
                raise DjangoValidationError(_("The model returned malformed JSON")) from exc
    else:
        params = raw
    if not isinstance(params, dict):
        raise DjangoValidationError(_("The model returned malformed JSON"))
    summary = str(params.pop(SUMMARY_PARAM, "") or "").strip()[:200]
    return params, summary


def drafts_from_tool_calls(user, tool_calls: list) -> dict:
    """原生 tool_calls → 既有 drafts 结构（双轨共用同一下游）。

    返回与 ``parse_draft`` 同构的结果；无工具调用 / 超量 / 参数畸形按可读校验错误处理。
    """
    from system.utils.ai_actions import MAX_DRAFTS_PER_REQUEST, _build_one_draft

    calls = [call for call in (tool_calls or []) if isinstance(call, dict) and call.get("name")]
    if not calls:
        raise DjangoValidationError(_("The model did not return an actionable request"))
    if len(calls) > MAX_DRAFTS_PER_REQUEST:
        raise DjangoValidationError(_("Too many actions requested (max {})").format(MAX_DRAFTS_PER_REQUEST))
    drafts = []
    for index, call in enumerate(calls, start=1):
        params, summary = _tool_call_params(call)
        drafts.append(_build_one_draft(user, {"action": call.get("name"), "params": params, "summary": summary}, index))
    return {"kind": "draft", "draft": drafts[0], "drafts": drafts}


def native_draft_result(user, message: str) -> tuple:
    """原生 function calling 轨道生成草稿：返回 ``(parse_draft 同构结果, 轨道标记)``。

    仅在 ``native_tools_enabled()``（能力探测通过 + 开关开启）为真时由调用方进入；
    工具调用不是散文，无需逐字流式，链路为一次性调用。
    """
    from system.utils.ai_config import structured_chat_client
    from system.utils.ai_tool_catalog import openai_tools
    from system.utils.ai_usage import tracked_chat_tools

    client, max_tokens = structured_chat_client()
    tools = openai_tools(user)
    if not tools:
        raise DjangoValidationError(_("The model did not return an actionable request"))
    result = tracked_chat_tools(
        user,
        "action",
        build_tool_messages(user, message),
        tools,
        client=client,
        track="native",  # AI-2 双轨对照：用量账本按轨道统计成功率
        tool_choice="auto",
        max_tokens=max_tokens,
    )
    return drafts_from_tool_calls(user, result.get("tool_calls") or []), "native"
