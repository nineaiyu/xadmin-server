#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 助手 NL 查数 actions（ViewSet mixin：与 assistant.py 的 AiAssistantViewSet 组合）。

业务编排已下沉 ai/services/nl_query/orchestrator.py（解释 / 流式 / 执行共用
门禁、校验、试算、审计与对话流落库）；本文件只留协议层：读参数、灰度门禁的
JSON 拒绝、SSE 包装与响应壳。URL 路径 / 权限点 / 行为与下沉前完全一致。
"""

from typing import Any

from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from ai.services.nl_query.orchestrator import (
    execute_dsl,
    interpret_gate,
    interpret_question,
    run_gate,
    stream_interpret_events,
)
from common.core.response import ApiResponse
from common.drf.renders import sse_response
from common.swagger.utils import get_default_response_schema


class AiNlQueryMixin:
    """NL 查数：解释（含流式）/ 执行（以调用者数据权限编译过滤）。"""

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="nl-query/interpret")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def nl_interpret(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """NL → 数据集 DSL

        白名单校验 + 试算预览计数（数据权限随调用者）
        """
        question = str(request.data.get("question") or "").strip()
        detail, datasets = interpret_gate(request.user, question)
        if detail:
            return ApiResponse(code=1001, detail=detail)
        ok, payload = interpret_question(request.user, question, datasets)
        if not ok:
            return ApiResponse(code=1001, detail=payload)
        return ApiResponse(data=payload)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="nl-query/interpret/stream")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def nl_interpret_stream(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """NL 查数解释流式

        SSE：meta → reasoning* → delta* → done | error

        与 `nl_interpret` 同口径：灰度/门禁/参数错误在响应头前返回 JSON 1001；
        流内以 reasoning 展示模型思考（本地思考型模型常见），done 携带与
        nl_interpret 相同结构的 `{dsl, dataset_name, preview_count, mode}`。
        """
        question = str(request.data.get("question") or "").strip()
        detail, datasets = interpret_gate(request.user, question)
        if detail:
            return ApiResponse(code=1001, detail=detail, content_type="application/json")
        return sse_response(stream_interpret_events(request.user, question, datasets))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="nl-query/run")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def nl_run(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """执行试算确认后的 DSL

        服务端重校验（不信任客户端回传）+ 审计
        """
        detail = run_gate()
        if detail:
            return ApiResponse(code=1001, detail=detail)
        ok, payload = execute_dsl(request.user, request.data.get("dsl"))
        if not ok:
            return ApiResponse(code=1001, detail=payload)
        return ApiResponse(data=payload)
