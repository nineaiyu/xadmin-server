#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 助手视图（ADR-023）：配置（Setting 体系）+ 状态 + 问答。

- 配置视图与邮件/LDAP 同构：POST create = 连接测试（真实 ping LLM）；
- ask/status 经菜单权限点门控（未授权 403）；问答链路不触生产数据。
"""

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.viewsets import GenericViewSet

from common.core.response import ApiResponse
from common.sdk.ai.chat import AiSdkError, ChatCompletionsClient
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from settings.serializers.ai import AiAssistantSettingSerializer
from settings.views.settings import BaseSettingViewSet
from system.models.ai import AiKnowledgeChunk
from system.models.dataset import Dataset
from system.utils.ai import ai_credentials, ask, is_configured, is_enabled

logger = get_logger(__name__)


class AiAssistantSettingViewSet(BaseSettingViewSet):
    """AI 助手配置与连接测试"""

    serializer_class = AiAssistantSettingSerializer
    category = "ai"

    def create(self, request, *args, **kwargs):
        """测试{cls}：按表单当前值实际 ping 一次 LLM。"""
        serializer = self.get_serializer_class()(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        keys = ("AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "AI_TIMEOUT")
        saved = {key: getattr(settings, key) for key in keys}
        try:
            for key in ("AI_BASE_URL", "AI_MODEL", "AI_TIMEOUT"):
                if key in request.data:
                    setattr(settings, key, data.get(key))
            api_key = data.get("AI_API_KEY") or settings.AI_API_KEY
            if not settings.AI_BASE_URL:
                return ApiResponse(code=1001, detail=_("Base URL is required"))
            if not (api_key and settings.AI_MODEL):
                return ApiResponse(code=1001, detail=_("API Key and model are required"))
            original_key = settings.AI_API_KEY
            settings.AI_API_KEY = api_key
            try:
                client = ChatCompletionsClient(ai_credentials())
                reply = client.chat([{"role": "user", "content": "ping"}])
            finally:
                settings.AI_API_KEY = original_key
        except AiSdkError as exc:
            return ApiResponse(code=1002, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 测试入口兜底
            logger.warning("AI connection test unexpected error", exc_info=True)
            return ApiResponse(code=1002, detail=str(exc))
        finally:
            for key, value in saved.items():
                setattr(settings, key, value)
        return ApiResponse(detail=_("AI provider OK: {}").format(reply[:80]))


class AiAssistantViewSet(GenericViewSet):
    """AI 使用/二开助手（基于 docs/ 知识库的 RAG 问答，不触生产数据）"""

    queryset = AiKnowledgeChunk.objects.none()

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="status")
    def status(self, request, *args, **kwargs):
        """助手状态：开关/配置/知识库规模（前端渲染未配置引导）。"""
        last_synced = AiKnowledgeChunk.objects.order_by("-synced_at").values_list("synced_at", flat=True).first()
        return ApiResponse(
            data={
                "enabled": is_enabled(),
                "configured": is_configured(),
                "chunks": AiKnowledgeChunk.objects.count(),
                "synced_at": last_synced.isoformat() if last_synced else "",
            }
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="nl-query/interpret")
    def nl_interpret(self, request, *args, **kwargs):
        """NL → 数据集 DSL（白名单校验）+ 试算预览计数（数据权限随调用者）。"""
        from system.utils.ai import is_enabled as ai_enabled_check
        from system.utils.nl_query import (
            audit_nl_query,
            build_interpret_prompt,
            parse_llm_json,
            validate_dsl,
            visible_datasets,
        )
        from common.sdk.ai.chat import AiSdkError

        question = str(request.data.get("question") or "").strip()
        if not question:
            return ApiResponse(code=1001, detail=_("Question cannot be empty"))
        if not settings.AI_NL_QUERY_ENABLED:
            return ApiResponse(code=1001, detail=_("NL query is not enabled"))
        if not ai_enabled_check():
            return ApiResponse(code=1001, detail=_("AI assistant is not enabled or configured"))

        datasets = visible_datasets(request.user)
        if not datasets:
            return ApiResponse(code=1001, detail=_("No visible datasets for NL query"))

        dsl: dict = {}
        normalized: dict = {}
        try:
            client = ChatCompletionsClient(ai_credentials())
            raw = client.chat(build_interpret_prompt(question, datasets))
            dsl = parse_llm_json(raw)
            normalized = validate_dsl(dsl, request.user)
            dataset = Dataset.objects.get(pk=normalized["dataset"])
            extra = [{"field": f["field"], "op": f["op"], "value": f["value"]} for f in normalized["filters"]]
            from system.utils.dataset import build_queryset

            queryset, model, __ = build_queryset(dataset, request.user, extra_filters=extra)
            preview_count = queryset.count()
        except DjangoValidationError as exc:
            audit_nl_query(request.user, "interpret", question, dsl, error="; ".join(exc.messages))
            return ApiResponse(code=1001, detail="; ".join(exc.messages))
        except AiSdkError as exc:
            audit_nl_query(request.user, "interpret", question, {}, error=str(exc))
            return ApiResponse(code=1001, detail=str(exc))
        audit_nl_query(request.user, "interpret", question, normalized, rows=preview_count)
        return ApiResponse(
            data={
                "dsl": normalized,
                "dataset_name": dataset.name,
                "preview_count": preview_count,
                "mode": normalized["mode"],
            }
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="nl-query/run")
    def nl_run(self, request, *args, **kwargs):
        """执行试算确认后的 DSL：服务端重校验（不信任客户端回传）+ 审计。"""
        from system.utils.ai import is_enabled as ai_enabled_check
        from system.utils.dataset import aggregate_dataset
        from system.utils.nl_query import audit_nl_query, validate_dsl

        dsl = request.data.get("dsl")
        if not settings.AI_NL_QUERY_ENABLED:
            return ApiResponse(code=1001, detail=_("NL query is not enabled"))
        if not ai_enabled_check():
            return ApiResponse(code=1001, detail=_("AI assistant is not enabled or configured"))
        try:
            normalized = validate_dsl(dsl if isinstance(dsl, dict) else {}, request.user)
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
            else:
                from system.utils.dataset import build_queryset

                extra = [{"field": f["field"], "op": f["op"], "value": f["value"]} for f in normalized["filters"]]
                queryset, model, columns = build_queryset(dataset, request.user, extra_filters=extra)
                result = {"columns": columns, "rows": list(queryset.values(*columns)[: normalized["limit"]])}
                rows = len(result["rows"])
        except DjangoValidationError as exc:
            audit_nl_query(request.user, "run", "", dsl if isinstance(dsl, dict) else {}, error="; ".join(exc.messages))
            return ApiResponse(code=1001, detail="; ".join(exc.messages))
        audit_nl_query(request.user, "run", "", normalized, rows=rows)
        return ApiResponse(data=result)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="ask")
    def ask(self, request, *args, **kwargs):
        """文档问答：回答引用文档出处；未启用/未配置/无命中/LLM 失败均转可读文案。"""
        question = str(request.data.get("question") or "")
        try:
            result = ask(question)
        except DjangoValidationError as exc:
            return ApiResponse(code=1001, detail="; ".join(exc.messages))
        return ApiResponse(data=result)
