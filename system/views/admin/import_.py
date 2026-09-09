#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""导入记录（下载中心「导入记录」页签）：查询 / 错误报告下载 / 日志 / 删除。

与导出下载中心同构：取值域不走通用数据权限（默认拒绝会让普通用户看不到自己
提交的导入），超管可见全部，普通用户仅本人提交记录；错误报告经 DRF 鉴权
下载而非 /media/ 直出（错误报告含业务数据行）。
"""

import os
from urllib.parse import quote

from django.http import FileResponse
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.plumbing import build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiResponse
from rest_framework.decorators import action
from rest_framework.filters import BaseFilterBackend, OrderingFilter

from common.core.filter import BaseFilterSet
from common.core.modelset import ListDeleteModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models.import_ import ImportRecord
from system.serializers.import_ import ImportRecordSerializer
from system.utils.task_log import read_task_log_chunk


class ImportRecordOwnerFilter(BaseFilterBackend):
    """导入记录取值域：超管全部，普通用户仅本人提交的导入记录。"""

    def filter_queryset(self, request, queryset, view):
        user = request.user
        if not user or not user.is_authenticated:
            return queryset.none()
        if user.is_superuser:
            return queryset
        return queryset.filter(creator=user)


class ImportRecordFilter(BaseFilterSet):
    """导入记录过滤：文件名模糊 + 状态/动作/目标模块精确 + 时间范围。"""

    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = ImportRecord
        fields = ["name", "status", "action", "module", "creator", "created_time"]


class ImportRecordViewSet(ListDeleteModelSet):
    """导入记录（下载中心）"""

    queryset = ImportRecord.objects.all()
    serializer_class = ImportRecordSerializer
    filterset_class = ImportRecordFilter
    # 覆盖默认 filter_backends：去掉 BaseDataPermissionFilter（默认拒绝），改走本人/超管取值域
    filter_backends = (DjangoFilterBackend, OrderingFilter, ImportRecordOwnerFilter)
    ordering = ["-created_time"]
    ordering_fields = ["created_time"]

    @extend_schema(responses=OpenApiResponse(build_basic_type(OpenApiTypes.BINARY)))
    @action(methods=["get"], detail=True, url_path="download")
    def download(self, request, *args, **kwargs):
        """下载失败行错误报告"""
        record = self.get_object()
        upload = record.error_report
        if not upload or not upload.filepath:
            return ApiResponse(code=1001, detail=_("Error report not found"))
        path = upload.filepath.path
        if not os.path.exists(path):
            return ApiResponse(code=1001, detail=_("Error report not found"))
        response = FileResponse(
            open(path, "rb"),
            as_attachment=True,
            filename=upload.filename,
            content_type=upload.mime_type or "application/octet-stream",
        )
        # RFC 5987：中文文件名需 UTF-8 编码声明，否则浏览器解出乱码
        response["Content-Disposition"] = "attachment; filename*=UTF-8''{}".format(quote(upload.filename))
        response["Access-Control-Expose-Headers"] = "Content-Disposition"
        return response

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
        record = self.get_object()
        data = read_task_log_chunk(
            record.pk,
            offset=request.query_params.get("offset") or 0,
            finished_hint=record.status in (ImportRecord.Status.SUCCESS, ImportRecord.Status.FAILURE),
        )
        return ApiResponse(data=data)
