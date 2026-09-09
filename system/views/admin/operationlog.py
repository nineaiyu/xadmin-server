#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : operationlog
# author : ly_13
# date : 6/27/2023

from django.db import models
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet, PkMultipleFilter
from common.core.modelset import ListDeleteModelSet, OnlyExportDataAction
from system.models import OperationLog
from system.serializers.log import OperationLogSerializer


class OperationLogFilter(BaseFilterSet):
    ipaddress = filters.CharFilter(field_name="ipaddress", lookup_expr="icontains")
    system = filters.CharFilter(field_name="system", lookup_expr="icontains")
    path = filters.CharFilter(field_name="path", lookup_expr="icontains")
    module = filters.CharFilter(field_name="module", lookup_expr="icontains")
    method = filters.CharFilter(field_name="method")
    # 行级变更历史：按对象主键精确回溯该行全部操作日志（中间件从 detail 路由
    # URL kwargs 提取 object_pk；模块维度可再用 module 过滤缩小范围）
    object_pk = filters.CharFilter(field_name="object_pk", lookup_expr="exact", label=_("Object pk"))
    path_exact = filters.CharFilter(field_name="path", lookup_expr="exact", label=_("Exact path"))
    response_code = filters.NumberFilter(field_name="response_code", label=_("Response code"))
    exec_time_min = filters.NumberFilter(field_name="exec_time", lookup_expr="gte", label=_("Exec time from"))
    exec_time_max = filters.NumberFilter(field_name="exec_time", lookup_expr="lte", label=_("Exec time to"))
    has_changes = filters.BooleanFilter(method="get_has_changes", label=_("Has field changes"))
    error_status = filters.BooleanFilter(method="get_error_status", label=_("Error status"))

    def get_error_status(self, queryset, name, value):
        if value is True:
            return queryset.exclude(status_code=1000)
        return queryset.filter(status_code=1000)

    def get_has_changes(self, queryset, name, value):
        # 字段级审计 diff（AUDIT_DIFF_MODELS 白名单模型的 update 路径写入）
        if value is True:
            return queryset.exclude(changes__isnull=True).exclude(changes="")
        return queryset.filter(models.Q(changes__isnull=True) | models.Q(changes=""))

    # 自定义的搜索模板，需要前端同时添加 userinfo 类型
    creator_id = PkMultipleFilter(input_type="api-search-user")

    class Meta:
        model = OperationLog
        fields = [
            "request_uuid",
            "module",
            "ipaddress",
            "system",
            "creator_id",
            "status_code",
            "response_code",
            "method",
            "path",
            "path_exact",
            "object_pk",
            "exec_time_min",
            "exec_time_max",
            "has_changes",
            "created_time",
            "error_status",
        ]


class OperationLogViewSet(ListDeleteModelSet, OnlyExportDataAction):
    """操作日志"""

    queryset = OperationLog.objects.all()
    serializer_class = OperationLogSerializer

    ordering_fields = ["created_time", "updated_time", "exec_time"]
    filterset_class = OperationLogFilter
