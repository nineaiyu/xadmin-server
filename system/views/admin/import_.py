#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""导入记录（下载中心「导入记录」页签）：查询 / 错误报告下载 / 日志 / 删除。

与导出下载中心同构：取值域不走通用数据权限（默认拒绝会让普通用户看不到自己
提交的导入），超管可见全部，普通用户仅本人提交记录；错误报告经 DRF 鉴权
下载而非 /media/ 直出（错误报告含业务数据行）。

取值域过滤、文件下载与任务日志读取的公共实现见 ``record_base.py``
（与导出记录共用，避免两份逐字重复的实现各自漂移）。
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
from system.models.import_ import ImportRecord
from system.serializers.import_ import ImportRecordSerializer
from system.views.admin.record_base import RecordFileDownloadMixin, RecordOwnerFilter, RecordTaskLogMixin


class ImportRecordFilter(BaseFilterSet):
    """导入记录过滤：文件名模糊 + 状态/动作/目标模块精确 + 时间范围。"""

    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = ImportRecord
        fields = ["name", "status", "action", "module", "creator", "created_time"]


class ImportRecordViewSet(RecordFileDownloadMixin, RecordTaskLogMixin, ListDeleteModelSet):
    """导入记录（下载中心）"""

    queryset = ImportRecord.objects.all()
    serializer_class = ImportRecordSerializer
    filterset_class = ImportRecordFilter
    # 覆盖默认 filter_backends：去掉 BaseDataPermissionFilter（默认拒绝），改走本人/超管取值域
    filter_backends = (DjangoFilterBackend, OrderingFilter, RecordOwnerFilter)
    ordering = ["-created_time"]
    ordering_fields = ["created_time"]

    download_file_field = "error_report"
    download_not_found_message = _("Error report not found")
    log_finished_statuses = (ImportRecord.Status.SUCCESS, ImportRecord.Status.FAILURE)

    @extend_schema(responses=OpenApiResponse(build_basic_type(OpenApiTypes.BINARY)))
    @action(methods=["get"], detail=True, url_path="download")
    def download(self, request, *args, **kwargs):
        """下载失败行错误报告"""
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
        """增量读取导入任务日志"""
        return self.read_record_task_log(request)
