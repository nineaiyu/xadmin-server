#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 助手 NL 查数 actions（ViewSet mixin：与 assistant.py 的 AiAssistantViewSet 组合）。

拆文件仅因行数门禁（500 行）：URL 路径 / 权限点 / 行为与拆分前完全一致。
"""

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from ai.utils.ai import readable_ai_error
from common.core.response import ApiResponse
from common.drf.renders import sse_response
from common.sdk.ai.chat import AiSdkError
from common.swagger.utils import get_default_response_schema


def _persist_nl_partial(user, reasoning_chunks: list, detail: str) -> dict:
    """NL 解释流中断（已有增量）：保留思考与可读原因（extra.partial），返回消息载荷。"""
    from ai.utils.ai_chat import message_payload, persist_message

    row = persist_message(
        user,
        "nl",
        "assistant",
        content=detail,
        reasoning="".join(reasoning_chunks),
        extra={"partial": detail},
    )
    return message_payload(row)


class AiNlQueryMixin:
    """NL 查数：解释（含流式）/ 执行（以调用者数据权限编译过滤）。"""

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="nl-query/interpret")
    def nl_interpret(self, request, *args, **kwargs):
        """NL → 数据集 DSL（白名单校验）+ 试算预览计数（数据权限随调用者）。"""
        from ai.utils.ai import is_enabled as ai_enabled_check
        from ai.utils.ai import structured_chat_client
        from ai.utils.ai_chat import message_payload, persist_message, system_error_message
        from ai.utils.ai_guard import guard_summary
        from ai.utils.nl_query import (
            audit_nl_query,
            build_interpret_prompt,
            parse_llm_json,
            validate_dsl,
            visible_datasets,
        )

        question = str(request.data.get("question") or "").strip()
        guard = guard_summary(prompt=question)
        if not question:
            return ApiResponse(code=1001, detail=_("Question cannot be empty"))
        if not settings.AI_NL_QUERY_ENABLED:
            return ApiResponse(code=1001, detail=_("NL query is not enabled"))
        if not ai_enabled_check():
            return ApiResponse(code=1001, detail=_("AI assistant is not enabled or configured"))

        from ai.utils.ai_usage import quota_error, tracked_chat

        quota = quota_error(request.user, "nl")
        if quota:
            system_error_message(request.user, "nl", quota)
            return ApiResponse(code=1001, detail=quota)

        datasets = visible_datasets(request.user)
        if not datasets:
            return ApiResponse(code=1001, detail=_("No visible datasets for NL query"))

        persist_message(request.user, "nl", "user", content=question)

        dsl: dict = {}
        normalized: dict = {}
        usage = None
        try:
            # 结构化输出上限走公共入口：未配置 max_tokens 时用安全默认（AI 配置页可调大）
            client, max_tokens = structured_chat_client()
            raw = tracked_chat(
                request.user,
                "nl",
                build_interpret_prompt(question, datasets, request.user),
                client=client,
                max_tokens=max_tokens,
            )
            usage = getattr(client, "last_usage", None)
            dsl = parse_llm_json(raw)
            normalized = validate_dsl(dsl, request.user)
            from dataset.services import Dataset

            dataset = Dataset.objects.get(pk=normalized["dataset"])
            extra = [{"field": f["field"], "op": f["op"], "value": f["value"]} for f in normalized["filters"]]
            from dataset.services import build_queryset

            queryset, model, __ = build_queryset(dataset, request.user, extra_filters=extra)
            preview_count = queryset.count()
        except DjangoValidationError as exc:
            detail = "; ".join(exc.messages)
            audit_nl_query(request.user, "interpret", question, dsl, error=detail, usage=usage, guard=guard)
            system_error_message(request.user, "nl", detail)
            return ApiResponse(code=1001, detail=detail)
        except AiSdkError as exc:
            detail = readable_ai_error(exc)
            audit_nl_query(request.user, "interpret", question, {}, error=detail, guard=guard)
            system_error_message(request.user, "nl", detail)
            return ApiResponse(code=1001, detail=detail)
        audit_nl_query(request.user, "interpret", question, normalized, rows=preview_count, usage=usage, guard=guard)
        result = {
            "dsl": normalized,
            "dataset_name": dataset.name,
            "preview_count": preview_count,
            "mode": normalized["mode"],
        }
        summary = str(_("Query ready: {} (preview {} rows)")).format(dataset.name, preview_count)
        row = persist_message(request.user, "nl", "assistant", content=summary, extra={"nl": result})
        return ApiResponse(data={**result, "message": message_payload(row)})

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="nl-query/interpret/stream")
    def nl_interpret_stream(self, request, *args, **kwargs):
        """NL 查数解释流式（SSE）：meta → reasoning* → delta* → done | error。

        与 `nl_interpret` 同口径：灰度/门禁/参数错误在响应头前返回 JSON 1001；
        流内以 reasoning 展示模型思考（本地思考型模型常见），done 携带与
        nl_interpret 相同结构的 `{dsl, dataset_name, preview_count, mode}`。
        """
        from ai.utils.ai import is_enabled as ai_enabled_check
        from ai.utils.ai import structured_chat_client
        from ai.utils.ai_chat import message_payload, persist_message, system_error_message
        from ai.utils.ai_guard import guard_summary
        from ai.utils.nl_query import (
            audit_nl_query,
            build_interpret_prompt,
            parse_llm_json,
            validate_dsl,
            visible_datasets,
        )

        question = str(request.data.get("question") or "").strip()
        guard = guard_summary(prompt=question)
        if not question:
            return ApiResponse(code=1001, detail=_("Question cannot be empty"), content_type="application/json")
        if not settings.AI_NL_QUERY_ENABLED:
            return ApiResponse(code=1001, detail=_("NL query is not enabled"), content_type="application/json")
        if not ai_enabled_check():
            return ApiResponse(
                code=1001,
                detail=_("AI assistant is not enabled or configured"),
                content_type="application/json",
            )
        from ai.utils.ai_usage import quota_error, tracked_chat_stream

        quota = quota_error(request.user, "nl")
        if quota:
            system_error_message(request.user, "nl", quota)
            return ApiResponse(code=1001, detail=quota, content_type="application/json")
        datasets = visible_datasets(request.user)
        if not datasets:
            return ApiResponse(code=1001, detail=_("No visible datasets for NL query"), content_type="application/json")

        user_row = persist_message(request.user, "nl", "user", content=question)

        def events():
            yield {"event": "meta", "data": {"question": question, "user_message": message_payload(user_row)}}
            chunks: list = []
            reasoning_chunks: list = []
            try:
                client, max_tokens = structured_chat_client()
                for item in tracked_chat_stream(
                    request.user,
                    "nl",
                    client,
                    build_interpret_prompt(question, datasets, request.user),
                    max_tokens=max_tokens,
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
                    raise DjangoValidationError(
                        _("The model did not provide a final answer; please retry or switch models")
                    )
                dsl = parse_llm_json(raw)
                normalized = validate_dsl(dsl, request.user)
                from dataset.services import Dataset

                dataset = Dataset.objects.get(pk=normalized["dataset"])
                extra = [{"field": f["field"], "op": f["op"], "value": f["value"]} for f in normalized["filters"]]
                from dataset.services import build_queryset

                queryset, model, __ = build_queryset(dataset, request.user, extra_filters=extra)
                preview_count = queryset.count()
            except DjangoValidationError as exc:
                detail = "; ".join(exc.messages)
                audit_nl_query(request.user, "interpret", question, {}, error=detail, guard=guard)
                payload = (
                    _persist_nl_partial(request.user, reasoning_chunks, detail)
                    if chunks or reasoning_chunks
                    else system_error_message(request.user, "nl", detail)
                )
                yield {"event": "error", "data": {"detail": detail, "message": payload}}
                return
            except AiSdkError as exc:
                detail = readable_ai_error(exc)
                audit_nl_query(request.user, "interpret", question, {}, error=detail, guard=guard)
                payload = (
                    _persist_nl_partial(request.user, reasoning_chunks, detail)
                    if chunks or reasoning_chunks
                    else system_error_message(request.user, "nl", detail)
                )
                yield {"event": "error", "data": {"detail": detail, "message": payload}}
                return
            audit_nl_query(request.user, "interpret", question, normalized, rows=preview_count, guard=guard)
            result = {
                "dsl": normalized,
                "dataset_name": dataset.name,
                "preview_count": preview_count,
                "mode": normalized["mode"],
            }
            # 模型正文是 JSON（机器结果），对话流落库用可读摘要 + 结构化 extra
            summary = str(_("Query ready: {} (preview {} rows)")).format(dataset.name, preview_count)
            row = persist_message(
                request.user,
                "nl",
                "assistant",
                content=summary,
                reasoning="".join(reasoning_chunks),
                extra={"nl": result},
            )
            yield {"event": "done", "data": {**result, "message": message_payload(row)}}

        return sse_response(events())

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="nl-query/run")
    def nl_run(self, request, *args, **kwargs):
        """执行试算确认后的 DSL：服务端重校验（不信任客户端回传）+ 审计。"""
        from ai.utils.ai import is_enabled as ai_enabled_check
        from ai.utils.ai_chat import message_payload, persist_message, system_error_message
        from ai.utils.nl_query import audit_nl_query, validate_dsl
        from dataset.services import aggregate_dataset

        dsl = request.data.get("dsl")
        if not settings.AI_NL_QUERY_ENABLED:
            return ApiResponse(code=1001, detail=_("NL query is not enabled"))
        if not ai_enabled_check():
            return ApiResponse(code=1001, detail=_("AI assistant is not enabled or configured"))
        try:
            normalized = validate_dsl(dsl if isinstance(dsl, dict) else {}, request.user)
            from dataset.services import Dataset

            dataset = Dataset.objects.get(pk=normalized["dataset"])
            if normalized["mode"] == "aggregate":
                result = aggregate_dataset(
                    dataset,
                    request.user,
                    group_by=normalized["group_by"],
                    metric=normalized["metric"],
                    date_trunc=normalized["date_trunc"] or None,
                    value_field=normalized["value_field"] or None,
                )
                rows = len(result["series"])
                summary = str(_("Query finished: {} groups")).format(rows)
            else:
                from dataset.services import build_queryset

                extra = [{"field": f["field"], "op": f["op"], "value": f["value"]} for f in normalized["filters"]]
                queryset, model, columns = build_queryset(dataset, request.user, extra_filters=extra)
                result = {"columns": columns, "rows": list(queryset.values(*columns)[: normalized["limit"]])}
                rows = len(result["rows"])
                summary = str(_("Query finished: {} rows")).format(rows)
        except DjangoValidationError as exc:
            detail = "; ".join(exc.messages)
            audit_nl_query(request.user, "run", "", dsl if isinstance(dsl, dict) else {}, error=detail)
            system_error_message(request.user, "nl", detail)
            return ApiResponse(code=1001, detail=detail)
        audit_nl_query(request.user, "run", "", normalized, rows=rows)
        # 运行结果作为独立消息落库（对话流可回看；操作审计另有 OperationLog）
        row = persist_message(request.user, "nl", "assistant", content=summary, extra={"nl_run": result})
        return ApiResponse(data={**result, "message": message_payload(row)})
