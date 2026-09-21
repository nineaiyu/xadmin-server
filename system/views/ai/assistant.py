#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 助手视图：配置（Setting 体系）+ 状态 + 对话历史 + 工具目录 + 文档问答（含流式）。

- 配置视图与邮件/LDAP 同构：POST create = 连接测试（真实 ping LLM）；
- ask/status/history/tools 经菜单权限点门控（未授权 403）；问答链路不触生产数据；
- 对话持久化：三入口消息落 AiChatMessage（system/utils/ai_chat.py 收口），
  流式 done/error 载荷携带持久化消息（前端以服务端载荷为准，刷新可续看）；
- NL 查数与受限动作执行拆至同目录 mixin（nl_query.py / actions.py，仅行数门禁，
  URL 与权限点不变）。
"""

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet

from common.core.response import ApiResponse
from common.drf.renders import SseRendererMixin, sse_response
from common.sdk.ai.chat import AiSdkError, ChatCompletionsClient
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from settings.serializers.ai import AiAssistantSettingSerializer
from settings.views.settings import BaseSettingViewSet
from system.models.ai import AiKnowledgeChunk
from system.utils.ai import ask, is_configured, is_enabled
from system.views.ai.actions import AiActionExecuteMixin
from system.views.ai.nl_query import AiNlQueryMixin

logger = get_logger(__name__)


class AiAssistantSettingViewSet(BaseSettingViewSet):
    """AI 助手配置与连接测试"""

    serializer_class = AiAssistantSettingSerializer
    category = "ai"

    @staticmethod
    def _test_credentials(data: dict) -> dict:
        """连接测试凭据：表单值 → 激活档案 → Setting 逐项兜底。

        不直接走 ``ai_credentials()``（档案优先）：已有激活档案时表单值会被完全
        忽略——用户在配置页填了新地址点「测试连接」，实际测的却是档案，结果误导。
        未提交的字段仍按「档案 → Setting」兜底，保持部分填写可测。
        """
        from system.utils.ai import active_profile

        profile = active_profile()

        def pick(form_key: str, profile_attr: str):
            value = data.get(form_key)
            if value not in (None, ""):
                return value
            if profile is not None:
                value = getattr(profile, profile_attr, None)
                if value not in (None, ""):
                    return value
            return getattr(settings, form_key, None)

        api_key = ""
        if data.get("AI_API_KEY"):
            api_key = str(data["AI_API_KEY"])
        elif profile is not None:
            api_key = profile.api_key_plain
        if not api_key:
            api_key = str(getattr(settings, "AI_API_KEY", "") or "")
        return {
            "base_url": pick("AI_BASE_URL", "base_url"),
            "api_key": api_key,
            "model": pick("AI_MODEL", "model"),
            "timeout": pick("AI_TIMEOUT", "timeout"),
            "max_retries": 0,
        }

    def create(self, request, *args, **kwargs):
        """测试{cls}：按表单当前值实际 ping 一次 LLM（表单缺省项按档案/Setting 兜底）。"""
        serializer = self.get_serializer_class()(data=request.data)
        serializer.is_valid(raise_exception=True)
        creds = self._test_credentials(dict(request.data))
        if not creds["base_url"]:
            return ApiResponse(code=1001, detail=_("Base URL is required"))
        if not (creds["api_key"] and creds["model"]):
            return ApiResponse(code=1001, detail=_("API Key and model are required"))
        try:
            client = ChatCompletionsClient(creds)
            reply = client.chat([{"role": "user", "content": "ping"}])
        except AiSdkError as exc:
            return ApiResponse(code=1002, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 测试入口兜底
            logger.warning("AI connection test unexpected error", exc_info=True)
            return ApiResponse(code=1002, detail=str(exc))
        return ApiResponse(detail=_("AI provider OK: {}").format(reply[:80]))


class AiAssistantViewSet(AiNlQueryMixin, AiActionExecuteMixin, SseRendererMixin, GenericViewSet):
    """AI 使用/二开助手（基于 docs/ 知识库的 RAG 问答，不触生产数据）

    actions：status/metrics/ask/ask_stream（本文件）+ nl-query/*（nl_query.py mixin）
    + action/execute（actions.py mixin），组合后 URL 与拆分前一致。
    SSE 协商与响应装配走公共件（SseRendererMixin / sse_response）。
    """

    queryset = AiKnowledgeChunk.objects.none()

    #: 需要 SSE 协商的流式 action（三入口各自的流式端点）
    sse_actions = ("ask_stream", "nl_interpret_stream", "action_interpret_stream")

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="status")
    def status(self, request, *args, **kwargs):
        """助手状态：开关/配置/知识库规模/动作可用性（前端渲染未配置引导与 /do 提示）。"""
        from system.utils.ai_actions import ai_action_enabled, available_actions

        last_synced = AiKnowledgeChunk.objects.order_by("-synced_at").values_list("synced_at", flat=True).first()
        action_ready = ai_action_enabled() and is_enabled()
        return ApiResponse(
            data={
                "enabled": is_enabled(),
                "configured": is_configured(),
                "chunks": AiKnowledgeChunk.objects.count(),
                "synced_at": last_synced.isoformat() if last_synced else "",
                "action_enabled": action_ready,
                "actions": [{"key": spec.key, "label": str(spec.label)} for spec in available_actions(request.user)]
                if action_ready
                else [],
            }
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="metrics")
    def metrics(self, request, *args, **kwargs):
        """AI 调用观测：近 N 天用量 / 成功率 / 日趋势 / 类型分布 / Top 用户。

        数据源 = OperationLog(auth_type=ai)：AI:ask（文档问答）/ AI:nl_query（NL 查数）/
        AI:action（受限动作）；权限点与 status 共用同一路径正则（`(status|metrics)$`，
        见菜单种子），不新增权限点。
        """
        from datetime import timedelta

        from django.db.models import Count
        from django.db.models.functions import TruncDate
        from django.utils import timezone

        from system.models import OperationLog, UserInfo

        try:
            days = int(request.query_params.get("days") or 30)
        except (TypeError, ValueError):
            days = 30
        days = max(1, min(days, 90))
        since = (timezone.now() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

        base = OperationLog.objects.filter(
            auth_type=OperationLog.AuthType.AI,
            created_time__gte=since,
        )
        total = base.count()
        failed = base.exclude(status_code=1000).count()

        module_labels = {"AI:ask": "文档问答", "AI:nl_query": "NL 查数", "AI:action": "受限动作"}
        by_module = [
            {
                "module": row["module"] or "",
                "label": module_labels.get(row["module"] or "", row["module"] or "未知"),
                "count": row["count"],
            }
            for row in base.values("module").annotate(count=Count("id")).order_by("-count")
        ]
        by_day = [
            {"date": row["day"].isoformat(), "module": row["module"] or "", "count": row["count"]}
            for row in base.annotate(day=TruncDate("created_time"))
            .values("day", "module")
            .annotate(count=Count("id"))
            .order_by("day")
        ]
        top_rows = (
            base.exclude(object_pk__isnull=True)
            .exclude(object_pk="")
            .values("object_pk")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )
        name_map = {
            str(pk): username
            for pk, username in UserInfo.objects.filter(pk__in=[row["object_pk"] for row in top_rows]).values_list(
                "pk", "username"
            )
        }
        # token 用量（成本维度）：解析窗口内审计 changes 的 usage 字段（缺 usage 的记录记 0）
        import json as _json

        prompt_tokens = completion_tokens = 0
        for raw_changes in base.values_list("changes", flat=True):
            try:
                usage = (_json.loads(raw_changes or "{}") or {}).get("usage") or {}
            except (TypeError, ValueError):
                continue
            try:
                prompt_tokens += int(usage.get("prompt_tokens") or 0)
                completion_tokens += int(usage.get("completion_tokens") or 0)
            except (TypeError, ValueError):
                continue
        return ApiResponse(
            data={
                "days": days,
                "total": total,
                "success": total - failed,
                "failed": failed,
                "by_module": by_module,
                "by_day": by_day,
                "top_users": [
                    {"username": name_map.get(row["object_pk"], row["object_pk"][:12]), "count": row["count"]}
                    for row in top_rows
                ],
                "tokens": {
                    "prompt": prompt_tokens,
                    "completion": completion_tokens,
                    "total": prompt_tokens + completion_tokens,
                },
            }
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="history")
    def history(self, request, *args, **kwargs):
        """助手页对话历史：按入口（feature）分页，只返回当前用户自己的消息流。

        契约：时间正序返回最近一页，``has_more`` 为真时用 ``before_id=最早一条 id``
        继续向上翻页（与聊天室历史同口径）。
        """
        from system.utils.ai_chat import load_history

        data = load_history(
            request.user,
            request.query_params.get("feature"),
            before_id=request.query_params.get("before_id"),
            limit=request.query_params.get("limit"),
        )
        return ApiResponse(data=data)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="tools")
    def tools(self, request, *args, **kwargs):
        """统一工具目录（MCP tools/list 等价的标准化描述）。

        输出当前用户**有权执行**的全部系统动作（白名单注册表），每条包含
        ``name / description / inputSchema``（JSON Schema）；LLM 的 function
        calling、外部 MCP 客户端或二开脚本可共用这一份目录——机制说明见
        ``system/utils/ai_actions.py`` 的模块注释（新增能力 = 加一条声明）。
        """
        from system.utils.ai_actions import ai_action_enabled
        from system.utils.ai_tool_catalog import tool_catalog

        enabled = ai_action_enabled()
        return ApiResponse(
            data={
                "action_enabled": enabled,
                "tools": tool_catalog(request.user) if enabled else [],
            }
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="ask")
    def ask(self, request, *args, **kwargs):
        """文档问答（非流式）：回答引用文档出处；未启用/未配置/无命中/LLM 失败均转可读文案。

        对话持久化与流式端点同口径（user 消息 + assistant/system 消息）。
        """
        from system.utils.ai_actions import audit_ai_ask
        from system.utils.ai_chat import message_payload, persist_message, system_error_message

        question = str(request.data.get("question") or "")
        try:
            result = ask(question)
        except DjangoValidationError as exc:
            detail = "; ".join(exc.messages)
            audit_ai_ask(request.user, question, ok=False, detail=detail)
            persist_message(request.user, "docs", "user", content=question)
            system_error_message(request.user, "docs", detail)
            return ApiResponse(code=1001, detail=detail)
        usage = result.pop("_usage", None)
        audit_ai_ask(request.user, question, ok=True, usage=usage)
        persist_message(request.user, "docs", "user", content=question)
        row = persist_message(
            request.user,
            "docs",
            "assistant",
            content=result.get("answer") or "",
            extra={"sources": result.get("sources") or []},
        )
        return ApiResponse(data={**result, "message": message_payload(row)})

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="ask/stream")
    def ask_stream(self, request, *args, **kwargs):
        """文档问答流式（SSE）：事件序 meta → reasoning* → delta* → done | error。

        与 `ask` 同口径：门禁/校验错误在响应头发出前返回 JSON 1001（前端按普通
        接口错误提示）；流内失败（LLM 中断/只有思考）转 error 事件（头已发出，
        无法再改状态码）。事件载荷：reasoning/delta 为 `{"delta": "..."}`，
        done 为 `{"answer", "sources"}`。
        """
        from system.utils.ai import ask_stream as ai_ask_stream
        from system.utils.ai import prepare_ask
        from system.utils.ai_actions import audit_ai_ask
        from system.utils.ai_chat import message_payload, persist_message, system_error_message

        question = str(request.data.get("question") or "")
        try:
            messages, sources = prepare_ask(question)
        except DjangoValidationError as exc:
            detail = "; ".join(exc.messages)
            audit_ai_ask(request.user, question, ok=False, detail=detail)
            return ApiResponse(code=1001, detail=detail, content_type="application/json")

        # 校验通过即落用户消息：即使流中断，刷新后也能看到本轮提问
        user_row = persist_message(request.user, "docs", "user", content=question)

        def events():
            yield {"event": "meta", "data": {"question": question, "user_message": message_payload(user_row)}}
            content_chunks: list = []
            reasoning_chunks: list = []
            try:
                for item in ai_ask_stream(messages, sources):
                    kind = item.get("type")
                    if kind == "reasoning":
                        reasoning_chunks.append(item["text"])
                        yield {"event": "reasoning", "data": {"delta": item["text"]}}
                    elif kind == "content":
                        content_chunks.append(item["text"])
                        yield {"event": "delta", "data": {"delta": item["text"]}}
                    elif kind == "done":
                        audit_ai_ask(request.user, question, ok=True)
                        row = persist_message(
                            request.user,
                            "docs",
                            "assistant",
                            content=item["answer"],
                            reasoning="".join(reasoning_chunks),
                            extra={"sources": item["sources"]},
                        )
                        yield {
                            "event": "done",
                            "data": {
                                "answer": item["answer"],
                                "sources": item["sources"],
                                "message": message_payload(row),
                            },
                        }
            except DjangoValidationError as exc:
                detail = "; ".join(exc.messages)
                audit_ai_ask(request.user, question, ok=False, detail=detail)
                if content_chunks or reasoning_chunks:
                    # 已有增量后中断：保留部分内容（与前端「已到达增量保留」一致）
                    row = persist_message(
                        request.user,
                        "docs",
                        "assistant",
                        content="".join(content_chunks) or detail,
                        reasoning="".join(reasoning_chunks),
                        extra={"partial": detail},
                    )
                    yield {"event": "error", "data": {"detail": detail, "message": message_payload(row)}}
                else:
                    yield {
                        "event": "error",
                        "data": {"detail": detail, "message": system_error_message(request.user, "docs", detail)},
                    }

        return sse_response(events())
