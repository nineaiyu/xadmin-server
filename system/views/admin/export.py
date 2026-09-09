#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""导出下载中心：异步导出记录的查询 / 下载 / 日志 / 删除。

取值域不走通用数据权限（默认拒绝会让普通用户看不到自己提交的导出）：
超管可见全部，普通用户仅本人提交记录。下载经 DRF 鉴权而非 /media/ 直出，
避免导出文件（常含敏感数据）被拿到 URL 即可任意下载。
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
from system.models.export import ExportRecord
from system.serializers.export import ExportRecordSerializer
from system.utils.task_log import read_task_log_chunk


class ExportRecordOwnerFilter(BaseFilterBackend):
    """下载中心取值域：超管全部，普通用户仅本人提交的导出记录。"""

    def filter_queryset(self, request, queryset, view):
        user = request.user
        if not user or not user.is_authenticated:
            return queryset.none()
        if user.is_superuser:
            return queryset
        return queryset.filter(creator=user)


class ExportRecordFilter(BaseFilterSet):
    """下载中心过滤：文件名模糊 + 状态/格式/触发人精确 + 时间范围。"""

    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = ExportRecord
        fields = ["name", "status", "file_format", "creator", "created_time"]


class ExportRecordViewSet(ListDeleteModelSet):
    """导出下载中心"""

    queryset = ExportRecord.objects.all()
    serializer_class = ExportRecordSerializer
    filterset_class = ExportRecordFilter
    # 覆盖默认 filter_backends：去掉 BaseDataPermissionFilter（默认拒绝），改走本人/超管取值域
    filter_backends = (DjangoFilterBackend, OrderingFilter, ExportRecordOwnerFilter)
    ordering = ["-created_time"]
    ordering_fields = ["created_time"]

    @extend_schema(responses=OpenApiResponse(build_basic_type(OpenApiTypes.BINARY)))
    @action(methods=["get"], detail=True, url_path="download")
    def download(self, request, *args, **kwargs):
        """下载导出文件"""
        record = self.get_object()
        upload = record.file
        if not upload or not upload.filepath:
            return ApiResponse(code=1001, detail=_("Export file not found"))
        path = upload.filepath.path
        if not os.path.exists(path):
            return ApiResponse(code=1001, detail=_("Export file not found"))
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
        """增量读取导出任务日志"""
        record = self.get_object()
        data = read_task_log_chunk(
            record.pk,
            offset=request.query_params.get("offset") or 0,
            finished_hint=record.status in (ExportRecord.Status.SUCCESS, ExportRecord.Status.FAILURE),
        )
        return ApiResponse(data=data)
