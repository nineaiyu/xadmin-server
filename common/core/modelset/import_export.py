#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""导入导出 Action：文件导出（export-data）与数据导入（import-data）。

含 Celery 异步导入分发（run_view_by_celery_task）。拆分自 modelset.py（T2.1）。
"""

import itertools
import json
import math
import uuid
from typing import Callable

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiRequest, OpenApiResponse
from rest_framework.decorators import action

from common.core.modelset.crud import CreateAction, ListAction, UpdateAction
from common.core.response import ApiResponse
from common.core.utils import has_self_fields, topological_sort
from common.drf.renders.csv import CSVFileRenderer
from common.drf.renders.excel import ExcelFileRenderer
from common.tasks import background_task_view_set_job
from common.utils import get_logger

logger = get_logger(__name__)


def run_view_by_celery_task(view, request, kwargs, data, batch_length=100):
    task = kwargs.get(
        "task", request.query_params.get("task", "true").lower() in ["true", "1", "yes"]
    )  # 默认为任务异步导入
    if task:
        view_str = f"{view.__class__.__module__}.{view.__class__.__name__}"
        meta = request.META
        task_id = uuid.uuid4()
        if isinstance(data, dict):
            data = [data]
        meta["task_count"] = math.ceil(len(data) / batch_length)
        meta["action"] = view.action
        try:
            # 检查Celery是否可用，如果不可用则直接执行任务
            from server.celery import app

            inspect = app.control.inspect()
            active_workers = inspect.active()
            if active_workers is None or not active_workers:
                # 没有活跃的worker，直接执行任务
                logger.warning("No active Celery workers found, executing task directly")
                return None  # 返回None表示需要直接执行
            for index, batch in enumerate(itertools.batched(data, batch_length)):
                meta["task_id"] = f"{task_id}_{index}"
                meta["task_index"] = index
                res = background_task_view_set_job.apply_async(
                    args=(view_str, meta, json.dumps(batch), view.action_map), task_id=meta["task_id"]
                )
                logger.info(f"add {view_str} task success. {res}")
            return ApiResponse(detail=_("Task add success"))
        except Exception as e:
            logger.error(f"Celery task submission failed: {e}, executing task directly")
            return None  # 如果提交任务失败，也返回None表示需要直接执行
    return None  # 如果task参数为false，直接执行


class OnlyExportDataAction(ListAction):
    @extend_schema(
        parameters=[
            OpenApiParameter(name="type", required=True, enum=["xlsx", "csv"]),
        ],
        responses={200: OpenApiResponse(build_basic_type(OpenApiTypes.BINARY))},
    )
    @action(methods=["get"], detail=False, url_path="export-data")
    def export_data(self, request, *args, **kwargs):
        """导出{cls}数据"""
        self.format_kwarg = request.query_params.get("type", "xlsx")
        request.no_cache = True  # 防止自定义缓存数据
        self.renderer_classes = [ExcelFileRenderer, CSVFileRenderer]
        request.accepted_renderer = None
        data = self.list(request, *args, **kwargs)
        return data


class ImportExportDataAction(CreateAction, UpdateAction, OnlyExportDataAction):
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
            batch_length = 99999999 if self_field else 100
            response = run_view_by_celery_task(self, request, kwargs, data, batch_length)
            if response:
                return response

        # 同步导入数据
        act = request.query_params.get("action")
        ignore_error = request.query_params.get("ignore_error", "false") == "true"
        if act and data:
            count = 0
            if act == "create":
                for item in data:
                    serializer = self.get_serializer(data=item)
                    serializer.is_valid(raise_exception=not ignore_error)
                    if serializer.errors and ignore_error:
                        continue
                    self.perform_create(serializer)
                    count += 1
            elif act == "update":
                queryset = self.filter_queryset(self.get_queryset())
                for item in data:
                    instance = queryset.filter(pk=item.get("pk")).first()
                    if not instance:
                        continue
                    serializer = self.get_serializer(instance, data=item, partial=True)
                    serializer.is_valid(raise_exception=not ignore_error)
                    if serializer.errors and ignore_error:
                        continue
                    self.perform_update(serializer)
                    count += 1
            return ApiResponse(detail=_("Operation successful. Import {} data").format(count))
        return ApiResponse(detail=_("Operation failed. Abnormal data"), code=1001)
