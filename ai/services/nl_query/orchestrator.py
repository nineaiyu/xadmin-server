#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""NL 查数编排（业务链路）：视图只留协议转换与灰度 JSON 拒绝。

解释 / 流式解释 / 执行三条链路的核心步骤此前内联在 ``ai/views/nl_query.py``
（三份近似实现、视图越层直连 dataset 查询），归位到本模块：

- 前置门禁（灰度 / AI 可用 / 配额 / 可见数据集）→ :func:`interpret_gate` /
  :func:`run_gate`；
- 解释核心（LLM → DSL 白名单校验 → 试算预览 → 审计 → 对话流落库）→
  :func:`interpret_question` / :func:`stream_interpret_events`；
- 执行核心（服务端重校验 → 聚合 / 取行 → 审计 → 落库）→ :func:`execute_dsl`。

安全口径不变：LLM 输出按不可信输入处理（白名单闭包），执行走数据集 contracts
（数据权限编译器强制过滤），全程审计（OperationLog module=AI:nl_query）。
"""

from collections.abc import Iterator
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

from ai.services.assistant.ai import readable_ai_error
from ai.services.nl_query.nl_query import (
    audit_nl_query,
    bounded_preview_count,
    build_interpret_prompt,
    parse_llm_json,
    validate_dsl,
    visible_datasets,
)
from dataset.contracts import Dataset, aggregate_dataset, build_queryset, execute_dataset
from integrations.sdk.ai.chat import AiSdkError


def preview_text(count: int, capped: bool) -> str:
    """预览计数文案：截断时标注「≥ N」（探测上限见 ai.services.nl_query.nl_query.PREVIEW_PROBE_LIMIT）。"""
    return f"≥{count}" if capped else str(count)


def interpret_gate(user: Any, question: str) -> tuple[str | None, list[Any]]:
    """解释链路前置门禁：返回 (拒绝 detail 或 None, 可见数据集列表)。

    依次：问题非空 → NL 灰度 → AI 可用 → 配额（超限落一条系统错误消息供回看）→
    可见数据集非空。数据集列表同时供解释提示词使用，避免二次查询。
    """
    from ai.services.assistant.ai import is_enabled as ai_enabled_check
    from ai.services.assistant.ai_usage import quota_error

    if not question:
        return str(_("Question cannot be empty")), []
    if not settings.AI_NL_QUERY_ENABLED:
        return str(_("NL query is not enabled")), []
    if not ai_enabled_check():
        return str(_("AI assistant is not enabled or configured")), []
    quota = quota_error(user, "nl")
    if quota:
        from ai.services.assistant.ai_chat import system_error_message

        system_error_message(user, "nl", quota)
        return quota, []
    datasets = visible_datasets(user)
    if not datasets:
        return str(_("No visible datasets for NL query")), []
    return None, datasets


def run_gate() -> str | None:
    """执行链路前置门禁（灰度 + AI 可用）。数据集可见性由 validate_dsl 白名单校验。"""
    from ai.services.assistant.ai import is_enabled as ai_enabled_check

    if not settings.AI_NL_QUERY_ENABLED:
        return str(_("NL query is not enabled"))
    if not ai_enabled_check():
        return str(_("AI assistant is not enabled or configured"))
    return None


def _validate_and_preview(user: Any, raw: str) -> tuple[Any, ...]:
    """LLM 原文 → DSL 解析与白名单校验 → 数据集解析 → 试算预览（失败抛 ValidationError）。"""
    dsl = parse_llm_json(raw)
    normalized = validate_dsl(dsl, user)
    dataset = Dataset.objects.get(pk=normalized["dataset"])
    extra = [{"field": f["field"], "op": f["op"], "value": f["value"]} for f in normalized["filters"]]
    queryset, model, __ = build_queryset(dataset, user, extra_filters=extra)
    # 预览与执行同口径：都按 DSL 的 limit 截断，避免「预览 N 行」与实际返回行数不一致
    preview_count, preview_capped = bounded_preview_count(queryset, model, limit=normalized["limit"])
    return dsl, normalized, dataset, preview_count, preview_capped


def _persist_nl_partial(user: Any, reasoning_chunks: list[Any], detail: str) -> dict[str, Any]:
    """NL 解释流中断（已有增量）：保留思考与可读原因（extra.partial），返回消息载荷。"""
    from ai.services.assistant.ai_chat import message_payload, persist_message

    row = persist_message(
        user,
        "nl",
        "assistant",
        content=detail,
        reasoning="".join(reasoning_chunks),
        extra={"partial": detail},
    )
    return message_payload(row)


def _interpret_payload(
    user: Any,
    normalized: dict[str, Any],
    dataset: Any,
    preview_count: int,
    preview_capped: bool,
    *,
    reasoning: str = "",
) -> dict[str, Any]:
    """解释结果载荷 + 对话流落库（可读摘要 + 结构化 extra）。"""
    from ai.services.assistant.ai_chat import message_payload, persist_message

    result = {
        "dsl": normalized,
        "dataset_name": dataset.name,
        "preview_count": preview_count,
        "preview_capped": preview_capped,
        "mode": normalized["mode"],
    }
    # 模型正文是 JSON（机器结果），对话流落库用可读摘要 + 结构化 extra
    summary = str(_("Query ready: {} (preview {} rows)")).format(
        dataset.name, preview_text(preview_count, preview_capped)
    )
    row = persist_message(user, "nl", "assistant", content=summary, reasoning=reasoning, extra={"nl": result})
    return {**result, "message": message_payload(row)}


def interpret_question(user: Any, question: str, datasets: list[Any]) -> tuple[bool, Any]:
    """非流式解释：LLM → 校验 → 试算预览 → 审计 → 落库。返回 (True, 载荷) / (False, detail)。"""
    from ai.services.assistant.ai import structured_chat_client
    from ai.services.assistant.ai_chat import persist_message, system_error_message
    from ai.services.assistant.ai_guard import guard_summary

    guard = guard_summary(prompt=question)
    persist_message(user, "nl", "user", content=question)
    parsed: dict[str, Any] = {}
    usage = None
    try:
        # 结构化输出上限走公共入口：未配置 max_tokens 时用安全默认（AI 配置页可调大）
        client, max_tokens = structured_chat_client()
        from ai.services.assistant.ai_usage import tracked_chat

        raw = tracked_chat(
            user, "nl", build_interpret_prompt(question, datasets, user), client=client, max_tokens=max_tokens
        )
        usage = getattr(client, "last_usage", None)
        parsed, normalized, dataset, preview_count, preview_capped = _validate_and_preview(user, raw)
    except DjangoValidationError as exc:
        detail = "; ".join(exc.messages)
        audit_nl_query(user, "interpret", question, parsed, error=detail, usage=usage, guard=guard)
        system_error_message(user, "nl", detail)
        return False, detail
    except AiSdkError as exc:
        detail = readable_ai_error(exc)
        audit_nl_query(user, "interpret", question, parsed, error=detail, guard=guard)
        system_error_message(user, "nl", detail)
        return False, detail
    audit_nl_query(user, "interpret", question, normalized, rows=preview_count, usage=usage, guard=guard)
    return True, _interpret_payload(user, normalized, dataset, preview_count, preview_capped)


def stream_interpret_events(user: Any, question: str, datasets: list[Any]) -> Iterator[dict[str, Any]]:
    """流式解释事件流：meta → reasoning* → delta* → done | error。

    done 携带与非流式同构的 ``{dsl, dataset_name, preview_count, preview_capped,
    mode, message}``；error 携带 ``{detail, message}``（已有增量时保留思考与
    可读原因，否则落系统错误消息）。
    """
    from ai.services.assistant.ai import structured_chat_client
    from ai.services.assistant.ai_chat import message_payload, persist_message, system_error_message
    from ai.services.assistant.ai_guard import guard_summary

    guard = guard_summary(prompt=question)
    user_row = persist_message(user, "nl", "user", content=question)
    yield {"event": "meta", "data": {"question": question, "user_message": message_payload(user_row)}}
    chunks: list[Any] = []
    reasoning_chunks: list[Any] = []
    parsed: dict[str, Any] = {}
    try:
        client, max_tokens = structured_chat_client()
        from ai.services.assistant.ai_usage import tracked_chat_stream

        for item in tracked_chat_stream(
            user, "nl", client, build_interpret_prompt(question, datasets, user), max_tokens=max_tokens
        ):
            text = item.get("text") or ""
            if not text:
                continue
            if item.get("type") == "reasoning":
                reasoning_chunks.append(text)
                yield {"event": "reasoning", "data": {"delta": text}}
            else:
                chunks.append(text)
                yield {"event": "delta", "data": {"delta": text}}
        raw = "".join(chunks)
        if not raw.strip():
            raise DjangoValidationError(_("The model did not provide a final answer; please retry or switch models"))
        parsed, normalized, dataset, preview_count, preview_capped = _validate_and_preview(user, raw)
    except (DjangoValidationError, AiSdkError) as exc:
        detail = "; ".join(exc.messages) if isinstance(exc, DjangoValidationError) else readable_ai_error(exc)
        audit_nl_query(user, "interpret", question, parsed, error=detail, guard=guard)
        payload = (
            _persist_nl_partial(user, reasoning_chunks, detail)
            if chunks or reasoning_chunks
            else system_error_message(user, "nl", detail)
        )
        yield {"event": "error", "data": {"detail": detail, "message": payload}}
        return
    audit_nl_query(user, "interpret", question, normalized, rows=preview_count, guard=guard)
    payload = _interpret_payload(
        user, normalized, dataset, preview_count, preview_capped, reasoning="".join(reasoning_chunks)
    )
    yield {"event": "done", "data": payload}


def execute_dsl(user: Any, dsl: Any) -> tuple[bool, Any]:
    """执行试算确认后的 DSL：服务端重校验（不信任客户端回传）+ 审计 + 结果落对话流。"""
    from ai.services.assistant.ai_chat import message_payload, persist_message, system_error_message

    parsed = dsl if isinstance(dsl, dict) else {}
    try:
        normalized = validate_dsl(parsed, user)
        dataset = Dataset.objects.get(pk=normalized["dataset"])
        extra = [{"field": f["field"], "op": f["op"], "value": f["value"]} for f in normalized["filters"]]
        if normalized["mode"] == "aggregate":
            result = aggregate_dataset(
                dataset,
                user,
                group_by=normalized["group_by"],
                metric=normalized["metric"],
                date_trunc=normalized["date_trunc"] or None,
                value_field=normalized["value_field"] or None,
                extra_filters=extra,
            )
            rows = len(result["series"])
            summary = str(_("Query finished: {} groups")).format(rows)
        else:
            # 复用数据集执行链：JSON 路径列的别名映射、字段权限、row_limit 与
            # 稳定排序只在这里实现一次；直接 values(*columns) 会把 data.x 当成模型字段
            result = execute_dataset(
                dataset,
                user,
                extra_filters=extra,
                max_rows=normalized["limit"],
            )
            rows = len(result["rows"])
            summary = str(_("Query finished: {} rows")).format(rows)
    except DjangoValidationError as exc:
        detail = "; ".join(exc.messages)
        audit_nl_query(user, "run", "", parsed, error=detail)
        system_error_message(user, "nl", detail)
        return False, detail
    audit_nl_query(user, "run", "", normalized, rows=rows)
    # 运行结果作为独立消息落库（对话流可回看；操作审计另有 OperationLog）
    row = persist_message(user, "nl", "assistant", content=summary, extra={"nl_run": result})
    return True, {**result, "message": message_payload(row)}
