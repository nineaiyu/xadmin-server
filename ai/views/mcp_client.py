#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""外部 MCP 服务器视图（MCP client 侧）：配置 CRUD + 工具同步 + 工具调用。

- ``sync``：连接服务器拉取 tools/list 存快照（失败记录 last_sync_error 并回可读报错）；
- ``call``：仅允许调用白名单内工具（allowed_tools 为空 = 全部禁止，fail-closed），
  参数体积收敛、结果截断回传并落审计（OperationLog module=AI:mcp:client）；
- 出站守卫与 Webhook 同口径（https / OUTBOUND_ALLOWED_HOSTS 白名单 / pinned 解析连接）。
"""

import json

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.viewsets import GenericViewSet

from ai.models.mcp import McpServer
from ai.serializers.mcp import McpServerSerializer
from ai.utils.mcp_client import MAX_ARGUMENTS_BYTES, McpClientError, audit_mcp_call, client_for, summarize_tool_result
from common.core.filter import BaseFilterSet
from common.core.modelset import (
    BaseViewSet,
    CreateAction,
    DestroyAction,
    DetailAction,
    ListAction,
    SearchColumnsAction,
    SearchFieldsAction,
    UpdateAction,
)
from common.core.response import ApiResponse
from common.core.throttle import AiThrottleMixin
from common.swagger.utils import get_default_response_schema

# 说明：调用参数 JSON 体积上限常量下沉到 ai/utils/mcp_client.py（MAX_ARGUMENTS_BYTES），
# AI 动作链路（ai_mcp_actions）与这里共用同一口径，避免两处漂移。


class McpServerFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")
    url = filters.CharFilter(field_name="url", lookup_expr="icontains")

    class Meta:
        model = McpServer
        fields = ["name", "url", "enabled", "creator", "created_time"]


class McpServerViewSet(
    AiThrottleMixin,
    BaseViewSet,
    CreateAction,
    DestroyAction,
    UpdateAction,
    ListAction,
    DetailAction,
    SearchFieldsAction,
    SearchColumnsAction,
    GenericViewSet,
):
    """外部 MCP 服务器（Streamable HTTP）：配置管理 + 工具同步 + 白名单工具调用。"""

    queryset = McpServer.objects.all()
    serializer_class = McpServerSerializer
    filterset_class = McpServerFilter
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    ordering = ["name"]
    ordering_fields = ["name", "enabled", "last_synced_time", "created_time"]
    select_related_fields = ("creator",)

    #: sync 为读操作（调试期高频），call 为真实外部执行（管理类重操作限流）
    ai_chat_actions = ("sync",)
    ai_admin_actions = ("call",)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="sync")
    def sync(self, request, *args, **kwargs):
        """同步工具清单（initialize → tools/list），结果存快照。"""
        server = self.get_object()
        if not server.enabled:
            return ApiResponse(code=1001, detail=_("MCP server is disabled"))
        try:
            tools = client_for(server).list_tools()
        except McpClientError as exc:
            server.last_sync_error = exc.message[:255]
            server.save(update_fields=["last_sync_error", "updated_time"])
            return ApiResponse(code=1001, detail=exc.message)
        server.tools_snapshot = tools
        server.last_synced_time = timezone.now()
        server.last_sync_error = ""
        server.save(update_fields=["tools_snapshot", "last_synced_time", "last_sync_error", "updated_time"])
        return ApiResponse(data={"tools": tools, "count": len(tools)})

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="call")
    def call(self, request, *args, **kwargs):
        """调用白名单内工具：body ``{tool, arguments}``（参数对象可选）。"""
        from rest_framework.exceptions import ValidationError

        server = self.get_object()
        if not server.enabled:
            return ApiResponse(code=1001, detail=_("MCP server is disabled"))
        tool = str(request.data.get("tool") or "").strip()
        arguments = request.data.get("arguments") or {}
        if not tool:
            return ApiResponse(code=1001, detail=_("Tool name is required"))
        if not isinstance(arguments, dict):
            raise ValidationError(_("Tool arguments must be an object"))
        try:
            size = len(json.dumps(arguments, ensure_ascii=False, default=str))
        except (TypeError, ValueError) as exc:
            raise ValidationError(_("Tool arguments are not serializable")) from exc
        if size > MAX_ARGUMENTS_BYTES:
            raise ValidationError(_("Tool arguments exceed the size limit"))
        if tool not in server.tool_names:
            audit_mcp_call(request.user, server, tool, False, "tool is not in the allowed list", arguments)
            return ApiResponse(code=1001, detail=_("Tool {} is not in the allowed list").format(tool))
        try:
            result = client_for(server).call_tool(tool, arguments)
        except McpClientError as exc:
            audit_mcp_call(request.user, server, tool, False, exc.message, arguments)
            return ApiResponse(code=1001, detail=exc.message)
        summary = summarize_tool_result(result)
        audit_mcp_call(request.user, server, tool, not summary["is_error"], summary["text"][:200], arguments)
        return ApiResponse(data={"tool": tool, **summary})
