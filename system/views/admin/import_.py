#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""导入记录（下载中心「导入记录」页签）：查询 / 错误报告下载 / 日志 / 删除。

与导出下载中心同构：取值域不走通用数据权限（默认拒绝会让普通用户看不到自己
提交的导入），超管可见全部，普通用户仅本人提交记录；错误报告经 DRF 鉴权
下载而非 /media/ 直出（错误报告含业务数据行）。

取值域过滤、文件下载与任务日志读取的公共实现见 ``record_base.py``
（与导出记录共用，避免两份逐字重复的实现各自漂移）。
"""

from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.plumbing import build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiResponse
from rest_framework.decorators import action
from rest_framework.filters import BaseFilterBackend, OrderingFilter

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, ListDeleteModelSet
from common.swagger.utils import get_default_response_schema
from system.models.import_ import ImportRecord, ImportTemplate
from system.serializers.import_ import ImportRecordSerializer, ImportTemplateSerializer
from system.views.admin.record_base import (
    RecordFileDownloadMixin,
    RecordOwnerFilter,
    RecordStatsMixin,
    RecordTaskLogMixin,
)


class ImportRecordFilter(BaseFilterSet):
    """导入记录过滤：文件名模糊 + 状态/动作/目标模块精确 + 时间范围。"""

    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = ImportRecord
        fields = ["name", "status", "action", "module", "creator", "created_time"]


class ImportRecordViewSet(RecordStatsMixin, RecordFileDownloadMixin, RecordTaskLogMixin, ListDeleteModelSet):
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


class ImportTemplateFilter(BaseFilterSet):
    """导入模板过滤：名称模糊 + 目标模型 / 共享标记精确。"""

    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = ImportTemplate
        fields = ["name", "model", "is_shared", "creator"]


class ImportTemplateScopeFilter(BaseFilterBackend):
    """模板取值域：超管全部；普通用户 = 共享模板 + 本人模板。

    不走通用数据权限（默认拒绝会让普通用户看不到自己的模板）。
    """

    def filter_queryset(self, request, queryset, view):
        user = request.user
        if not user or not user.is_authenticated:
            return queryset.none()
        if user.is_superuser:
            return queryset
        return queryset.filter(Q(is_shared=True) | Q(creator=user))


class ImportTemplateViewSet(BaseModelSet):
    """导入列映射模板（个人 / 全局共享两档，按目标模型隔离）"""

    queryset = ImportTemplate.objects.all()
    serializer_class = ImportTemplateSerializer
    filterset_class = ImportTemplateFilter
    filter_backends = (DjangoFilterBackend, OrderingFilter, ImportTemplateScopeFilter)
    ordering = ["-created_time"]
    ordering_fields = ["created_time"]

    def get_queryset(self):
        """共享模板对普通用户只读：写操作（改/删）看不到共享模板，直接 404。

        取值域过滤（读）仍包含共享模板——导入时套用共享模板是普通用户的核心用法。
        """
        queryset = super().get_queryset()
        request = getattr(self, "request", None)
        user = getattr(request, "user", None)
        if request and getattr(request, "method", None) in ("PUT", "PATCH", "DELETE"):
            if not getattr(user, "is_superuser", False):
                return queryset.filter(is_shared=False)
        return queryset
