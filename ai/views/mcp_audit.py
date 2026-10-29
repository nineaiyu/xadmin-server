#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MCP 调用审计报表（提案 P5）：OperationLog(module=AI:mcp:client) 的只读视图。

不建第二张表：调用流水由 ``audit_mcp_call`` 统一落审计，本视图集按 module +
object_pk 预过滤出单服务器的调用历史，工具 / 通道 / 结果经序列化器从 changes
JSON 提取。审计痕迹不可经 API 抹除——只读 + 详情 + 导出，无删除端点（与
操作日志页同口径）。
"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework.filters import OrderingFilter
from rest_framework.viewsets import GenericViewSet

from ai.serializers.mcp_audit import McpCallLogSerializer
from ai.services.mcp.mcp_audit import MCP_AUDIT_MODULE
from audit.services import OperationLog
from common.core.filter import BaseFilterSet
from common.core.modelset import DetailAction, OnlyExportDataAction, OnlyListModelSet
from common.core.pagination import KeysetPageNumber
from common.swagger.utils import get_default_response_schema


class McpCallLogFilter(BaseFilterSet):
    """报表过滤器：服务器（object_pk）/ 工具（changes JSON 提取列在列表行内
    过滤口径有限，工具维度经 controlled_lookup 不开放——工具名过滤在审计行
    changes JSON 内，DB 侧不可索引；服务器 / 时间 / 结果三维度够排障用）。"""

    object_pk = filters.CharFilter(field_name="object_pk", lookup_expr="exact", label=_("MCP server"))
    ok = filters.BooleanFilter(method="get_ok", label=_("Success"))

    def get_ok(self, queryset: Any, name: Any, value: Any) -> Any:
        from common.core.response import API_SUCCESS_CODE

        if value is True:
            return queryset.filter(status_code=API_SUCCESS_CODE)
        return queryset.exclude(status_code=API_SUCCESS_CODE)

    class Meta:
        model = OperationLog
        fields = ["object_pk", "ok", "created_time"]


class McpCallLogViewSet(OnlyListModelSet, DetailAction, OnlyExportDataAction, GenericViewSet):
    """MCP 调用审计

    单服务器调用流水（module=AI:mcp:client）：只读 + 详情 + 导出，不可删除
    """

    queryset = OperationLog.objects.filter(module=MCP_AUDIT_MODULE)
    serializer_class = McpCallLogSerializer
    pagination_class = KeysetPageNumber
    filterset_class = McpCallLogFilter
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    ordering_fields = ["created_time", "exec_time"]

    @extend_schema(responses=get_default_response_schema())
    def list(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """调用流水列表"""
        return super().list(request, *args, **kwargs)

    @extend_schema(responses=get_default_response_schema())
    def retrieve(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """调用详情"""
        return super().retrieve(request, *args, **kwargs)
