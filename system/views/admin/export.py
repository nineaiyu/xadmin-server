#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""导出下载中心：异步导出记录的查询 / 下载 / 日志 / 删除。

取值域不走通用数据权限（默认拒绝会让普通用户看不到自己提交的导出）：
超管可见全部，普通用户仅本人提交记录。下载经 DRF 鉴权而非 /media/ 直出，
避免导出文件（常含敏感数据）被拿到 URL 即可任意下载。

取值域过滤、文件下载与任务日志读取的公共实现见 ``record_base.py``
（与导入记录共用，避免两份逐字重复的实现各自漂移）。
"""

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.plumbing import build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiResponse
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter

from common.core.filter import BaseFilterSet
from common.core.modelset import ListDeleteModelSet
from common.swagger.utils import get_default_response_schema
from system.models.export import ExportRecord
from system.serializers.export import ExportRecordSerializer
from system.views.admin.record_base import RecordFileDownloadMixin, RecordOwnerFilter, RecordTaskLogMixin


class ExportRecordFilter(BaseFilterSet):
    """下载中心过滤：文件名模糊 + 状态/格式/触发人精确 + 时间范围。"""

    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = ExportRecord
        fields = ["name", "status", "file_format", "creator", "created_time"]


class ExportRecordViewSet(RecordFileDownloadMixin, RecordTaskLogMixin, ListDeleteModelSet):
    """导出下载中心"""

    queryset = ExportRecord.objects.all()
    serializer_class = ExportRecordSerializer
    filterset_class = ExportRecordFilter
    # 覆盖默认 filter_backends：去掉 BaseDataPermissionFilter（默认拒绝），改走本人/超管取值域
    filter_backends = (DjangoFilterBackend, OrderingFilter, RecordOwnerFilter)
    ordering = ["-created_time"]
    ordering_fields = ["created_time"]

    download_file_field = "file"
    download_not_found_message = _("Export file not found")
    log_finished_statuses = (ExportRecord.Status.SUCCESS, ExportRecord.Status.FAILURE)

    @extend_schema(responses=OpenApiResponse(build_basic_type(OpenApiTypes.BINARY)))
    @action(methods=["get"], detail=True, url_path="download")
    def download(self, request, *args, **kwargs):
        """下载导出文件"""
        return self.download_record_file()

    @extend_schema(
        responses=get_default_response_schema(
            {
                "offset": build_basic_type(OpenApiTypes.NUMBER),
                "finished": build_basic_type(OpenApiTypes.BOOL),
                "content": build_basic_type(OpenApiTypes.STR),
            }
        )
    )
    @action(methods=["get"], detail=True, url_path="log")
    def log(self, request, *args, **kwargs):
        """增量读取导出任务日志"""
        return self.read_record_task_log(request)
