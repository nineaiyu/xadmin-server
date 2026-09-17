#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""导入导出：组合动作（import-data 同步/异步分流）。"""

from collections.abc import Callable

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiRequest, OpenApiResponse, extend_schema
from rest_framework.decorators import action

from common.core.modelset.crud import CreateAction, UpdateAction
from common.core.response import ApiResponse
from common.core.utils import has_self_fields, topological_sort
from common.utils import get_logger

from .celery_utils import CELERY_IMPORT_DEFAULT_BATCH, CELERY_IMPORT_SINGLE_BATCH, run_view_by_celery_task
from .export_actions import OnlyExportDataAction
from .import_actions import ImportAsyncAction

logger = get_logger(__name__)


class ImportExportDataAction(CreateAction, UpdateAction, ImportAsyncAction, OnlyExportDataAction):
    filter_queryset: Callable
    get_queryset: Callable
    get_serializer: Callable

    @extend_schema(
        parameters=[
            OpenApiParameter(name="action", required=True, enum=["create", "update"]),
        ],
        request=OpenApiRequest(
            build_basic_type(OpenApiTypes.BINARY),
        ),
        responses={200: OpenApiResponse(build_basic_type(OpenApiTypes.BINARY))},
    )
    @action(methods=["post"], detail=False, url_path="import-data")
    @transaction.atomic
    def import_data(self, request, *args, **kwargs):
        """导入{cls}数据"""

        task = kwargs.get(
            "task", request.query_params.get("task", "true").lower() in ["true", "1", "yes"]
        )  # 默认为任务异步导入
        # 列映射必须在访问 request.data（触发文件解析）之前解析
        self._resolve_import_mapping(request)
        data = request.data

        # 处理数据格式，确保是列表格式
        if isinstance(data, dict):
            data = [data]

        # 检查是否存在自关联依赖
        self_field = has_self_fields(self.queryset.model, data[0].keys()) if data else None

        # 如果存在依赖关系，则对数据进行拓扑排序
        if self_field:
            data = topological_sort(data, parent=self_field)

        # 尝试使用异步任务导入
        if task and data:
            # 自关联数据经拓扑排序后必须按依赖顺序整体执行，不能分片（分片会打乱父子顺序）
            batch_length = CELERY_IMPORT_SINGLE_BATCH if self_field else CELERY_IMPORT_DEFAULT_BATCH
            response = run_view_by_celery_task(self, request, kwargs, data, batch_length)
            if response:
                return response

        # 同步导入数据
        # Deprecated：ignore_error=true 会跳过失败行（保持历史兼容语义），
        # 需要失败行定位/错误报告请改走 import-validate + import-async（异步导入 2.0）
        act = request.query_params.get("action")
        ignore_error = request.query_params.get("ignore_error", "false") == "true"
        if act and data:
            count, failed = self._sync_import(request, data, ignore_error)
            if failed:
                # 失败/跳过行数至少落到日志，避免"静默丢数"完全无迹可循
                logger.warning(f"sync import skipped {failed} rows. action:{act} ignore_error:{ignore_error}")
            return ApiResponse(detail=_("Operation successful. Import {} data").format(count))
        return ApiResponse(detail=_("Operation failed. Abnormal data"), code=1001)

    def _sync_import(self, request, data, ignore_error):
        """同步导入 create/update 两种动作，返回 ``(成功条数, 失败或跳过条数)``。"""
        act = request.query_params.get("action")
        count = 0
        failed = 0
        if act == "create":
            for item in data:
                serializer = self.get_serializer(data=item)
                serializer.is_valid(raise_exception=not ignore_error)
                if serializer.errors:
                    # raise_exception 已保证非 ignore_error 时不会走到这里
                    failed += 1
                    continue
                self.perform_create(serializer)
                count += 1
        elif act == "update":
            queryset = self.filter_queryset(self.get_queryset())
            for item in data:
                instance = queryset.filter(pk=item.get("pk")).first()
                if not instance:
                    failed += 1
                    continue
                serializer = self.get_serializer(instance, data=item, partial=True)
                serializer.is_valid(raise_exception=not ignore_error)
                if serializer.errors:
                    failed += 1
                    continue
                self.perform_update(serializer)
                count += 1
        return count, failed
