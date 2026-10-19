#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""原生 function calling 双轨：工具调用 → 既有草稿结构（自 ai_actions 拆出，仅行数门禁）。

双轨共用同一下游：本模块只做「工具调用 → drafts」的映射，逐项复用
``ai_actions._build_one_draft`` 的校验链（白名单 / 可用性 / 权限 / 参数规范化），
产出与 ``parse_draft`` 完全同构的结果；执行、审批、审计链路零改动。
"""

import datetime
import json
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _


def build_tool_messages(user: Any, message: str) -> list[Any]:
    """原生 tools 轨道的消息：动作目录经 tools 定义下发，不重复进 prompt。

    仍保留引用数据护栏声明：模型/工具定义之外的业务内容不可作为指令。
    """
    from ai.utils.ai_actions import MAX_DRAFTS_PER_REQUEST, MAX_MESSAGE_LENGTH
    from ai.utils.ai_guard import REFERENCE_GUARD_INSTRUCTION

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


def _tool_call_params(call: dict[str, Any]) -> tuple[Any, ...]:
    """tool_call arguments（JSON 字符串 / 对象）→ (params, summary)；畸形输出抛可读错误。"""
    from ai.utils.ai_tool_catalog import SUMMARY_PARAM

    raw = call.get("arguments")
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            params: Any = {}
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


def drafts_from_tool_calls(user: Any, tool_calls: list[Any]) -> dict[str, Any]:
    """原生 tool_calls → 既有 drafts 结构（双轨共用同一下游）。

    返回与 ``parse_draft`` 同构的结果；无工具调用 / 超量 / 参数畸形按可读校验错误处理。
    """
    from ai.utils.ai_actions import MAX_DRAFTS_PER_REQUEST, _build_one_draft

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


def draft_result_from_chat(result: dict[str, Any], user: Any) -> dict[str, Any]:
    """原生调用结果 → 草稿或澄清消息（与 ``parse_draft`` 同构）。

    三种模型行为都要接住（提示词约定「不可执行时用追问回复」，但不同模型
    对 tools 协议的执行程度不同）：
    1. 发起 tool_calls → 按工具调用映射草稿；
    2. 未发起工具调用、但按 prompt-JSON 契约输出 ``{"actions"|"action"|"message"}``
       → 复用 ``parse_draft`` 解析（混合模型：tools 定义在场时仍写 JSON）；
    3. 仅返回散文/其他 JSON → 按澄清消息透出（截断到 MAX_REASON_LENGTH）。
    无 tool_calls 且无 content 才是真正的不可执行。
    """
    from ai.utils.ai_actions import MAX_REASON_LENGTH, parse_draft
    from common.utils.ai_parse import AiOutputParseError, extract_json_object

    tool_calls = result.get("tool_calls") or []
    if tool_calls:
        return drafts_from_tool_calls(user, tool_calls)
    content = str(result.get("content") or "").strip()
    if not content:
        raise DjangoValidationError(_("The model did not return an actionable request"))
    try:
        payload = extract_json_object(content)
    except AiOutputParseError:
        return {"kind": "message", "message": content[:MAX_REASON_LENGTH]}
    if isinstance(payload, dict) and (
        payload.get("actions") is not None or payload.get("action") or payload.get("message")
    ):
        # 契约内 JSON：交给 parse_draft 做白名单/参数校验（校验失败按可读错误上抛）
        return parse_draft(content, user)
    return {"kind": "message", "message": content[:MAX_REASON_LENGTH]}


def native_draft_result_stream(user: Any, message: str) -> Any:
    """原生 function calling 轨道的流式形态：逐段产出 reasoning/content，末帧携带结果。

    与 ``native_draft_result`` 共用同一套工具目录、消息构造与结果校验；差异只在
    传输形态：思考增量与正文增量实时产出（``{"type": "reasoning"|"content"}``），
    工具调用分片由 SDK 累积成 ``{"type": "tool_calls"}``，最终产出
    ``{"type": "result", "result": <draft_result_from_chat 同构结果>}``。

    工具调用本身不逐字上屏（arguments 是 JSON 片段，不是散文）；模型若同时输出
    推理/正文，前端可立即看到增量，从而避免「长时间无输出」。
    """
    from ai.utils.ai_config import structured_chat_client
    from ai.utils.ai_tool_catalog import openai_tools
    from ai.utils.ai_usage import tracked_chat_stream

    client, max_tokens = structured_chat_client()
    tools = openai_tools(user)
    if not tools:
        raise DjangoValidationError(_("The model did not return an actionable request"))
    content_chunks: list[Any] = []
    tool_calls: list[Any] = []
    for item in tracked_chat_stream(
        user,
        "action",
        client,
        build_tool_messages(user, message),
        track="native",  # 双轨对照：用量账本按轨道统计成功率
        tools=tools,
        tool_choice="auto",
        max_tokens=max_tokens,
    ):
        kind = item.get("type")
        if kind == "tool_calls":
            tool_calls = item.get("tool_calls") or []
            continue
        if kind == "content":
            content_chunks.append(str(item.get("text") or ""))
        yield item
    result = draft_result_from_chat({"content": "".join(content_chunks), "tool_calls": tool_calls}, user)
    yield {"type": "result", "result": result}


def native_draft_result(user: Any, message: str) -> tuple[Any, ...]:
    """原生 function calling 轨道生成草稿（非流式入口：聊天室 ``/do`` 等）。

    仅在 ``native_tools_enabled()``（能力探测通过 + 开关开启）为真时由调用方进入；
    助手页的流式入口使用 ``native_draft_result_stream``（同一套目录与校验）。
    """
    from ai.utils.ai_config import structured_chat_client
    from ai.utils.ai_tool_catalog import openai_tools
    from ai.utils.ai_usage import tracked_chat_tools

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
        track="native",  # 双轨对照：用量账本按轨道统计成功率
        tool_choice="auto",
        max_tokens=max_tokens,
    )
    return draft_result_from_chat(result, user), "native"
