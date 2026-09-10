#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""导入导出 Action：文件导出（export-data）与数据导入（import-data）。

含 Celery 异步导入分发（run_view_by_celery_task）。拆分自 modelset.py。
"""

import itertools
import json
import math
import uuid
from typing import Callable

from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiRequest, OpenApiResponse
from rest_framework.decorators import action

from common.core.modelset.crud import CreateAction, ListAction, UpdateAction
from common.core.response import ApiResponse
from common.core.utils import has_self_fields, topological_sort
from common.swagger.utils import get_default_response_schema
from common.drf.renders.csv import CSVFileRenderer
from common.drf.renders.excel import ExcelFileRenderer
from common.tasks import background_task_view_set_job
from common.utils import get_logger

logger = get_logger(__name__)


def _flatten_row_errors(row, ser_errors, limit):
    """把 DRF serializer.errors 展开为字段级条目 [{row, field, message}]，最多 limit 条。

    嵌套序列化器（dict 值）无法定位单一字段，整体 JSON 序列化进 message。
    """
    items = []
    for field, msgs in (ser_errors or {}).items():
        if not isinstance(msgs, list):
            msgs = [msgs]
        for msg in msgs:
            if isinstance(msg, dict):
                msg = json.dumps(msg, ensure_ascii=False, default=str)
            items.append({"row": row, "field": field, "message": str(msg)[:200]})
            if len(items) >= limit:
                return items
    return items


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

    @extend_schema(
        request=OpenApiRequest(build_object_type(properties={"type": build_basic_type(OpenApiTypes.STR)})),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="export-async")
    def export_async(self, request, *args, **kwargs):
        """异步导出{cls}数据"""
        from django.apps import apps
        from django.db import transaction
        from django.utils import timezone as dj_timezone
        from django.utils.module_loading import import_string

        from common.core.config import SysConfig

        params = dict(request.data) if isinstance(request.data, dict) else {}
        for key, value in request.query_params.items():
            params.setdefault(key, value)
        file_format = params.get("type") or "xlsx"
        model = self.get_queryset().model
        name = "{}_{}".format(model._meta.model_name, dj_timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S"))
        # 跨 app 惰性取模型/任务：common 层不直接依赖 system（契约层约束，见 check_cross_app_imports）
        export_record_model = apps.get_model("system", "ExportRecord")
        # 同用户并发上限：导出是最重的后台任务，防止重复点击/脚本刷爆 worker
        max_running = SysConfig.EXPORT_ASYNC_MAX_RUNNING
        if max_running > 0:
            running = export_record_model.objects.filter(
                creator=request.user,
                status__in=[export_record_model.Status.PENDING, export_record_model.Status.RUNNING],
            ).count()
            if running >= max_running:
                return ApiResponse(
                    code=1001,
                    detail=_("Too many export tasks in progress (limit {}), please wait for them to finish").format(
                        max_running
                    ),
                )
        record = export_record_model.objects.create(
            name=name,
            module=str(model._meta.verbose_name),
            path=request.path,
            file_format=file_format,
            params=params,
        )
        args = [
            str(record.pk),
            f"{self.__class__.__module__}.{self.__class__.__name__}",
            params,
            getattr(request.user, "pk", None),
        ]
        task = import_string("system.tasks.async_export_data_task")
        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            # 测试/E2E：send_task/apply_async 在 eager 下不执行，改 apply 同步跑完
            task.apply(args=args, task_id=str(record.pk))
        else:
            transaction.on_commit(lambda: task.apply_async(args=args, task_id=str(record.pk)))
        return ApiResponse(
            data={"record_id": str(record.pk), "task_id": str(record.pk)},
            detail=_("Export task submitted"),
        )


class ImportAsyncAction(object):
    """导入前校验与异步导入（大数据量场景，记录与错误报告在下载中心获取）。

    协议与同步 import-data 完全同源：请求体即文件原始内容（Content-Type
    text/csv / text/xlsx），由同一套文件解析器（ExcelFileParser/CSVFileParser）
    解析为行数据；`action` 走查询参数。差异仅在后续处理：
    - import-validate：逐行校验不落库，同步返回错误行定位；
    - import-async：行数据序列化为 JSON 落 UploadFile(is_tmp=True)，任务内
      直接读行导入（大文件不塞 broker 消息，也不重复解析）。
    """

    def _get_rows_and_titles(self, request):
        """从文件解析器产物中取行数据与原表头。"""
        rows = request.data
        if isinstance(rows, dict):
            rows = [rows]
        pairs = getattr(request, "jms_context", {}).get("column_title_field_pairs") or []
        column_titles = [title for title, _field in pairs if title]
        return rows, column_titles

    def _import_context(self, request):
        """提取导入上下文：目标模型、视图路径、提交者。"""
        model = self.get_queryset().model
        view_path = f"{self.__class__.__module__}.{self.__class__.__name__}"
        return model, view_path, getattr(request.user, "pk", None)

    def _check_running_limit(self, request):
        """同用户并发上限（IMPORT_ASYNC_MAX_RUNNING，0=不限制），超限返回提示文案。"""
        from django.apps import apps

        from common.core.config import SysConfig

        max_running = SysConfig.IMPORT_ASYNC_MAX_RUNNING
        if max_running <= 0:
            return None
        import_record_model = apps.get_model("system", "ImportRecord")
        running = import_record_model.objects.filter(
            creator=request.user,
            status__in=[import_record_model.Status.PENDING, import_record_model.Status.RUNNING],
        ).count()
        if running >= max_running:
            return _("Too many import tasks in progress (limit {}), please wait for them to finish").format(max_running)
        return None

    @staticmethod
    def _save_rows_file(rows, user, filename):
        """行数据序列化为 JSON 落 UploadFile(is_tmp=True)，供任务内读取。"""
        from django.apps import apps
        from django.core.files.base import ContentFile

        upload_model = apps.get_model("system", "UploadFile")
        content = json.dumps(rows, ensure_ascii=False, default=str).encode("utf-8")
        instance = upload_model(
            filename=filename,
            filesize=len(content),
            mime_type="application/json",
            is_tmp=True,
            is_upload=False,
            creator=user,
        )
        instance.filepath.save(filename, ContentFile(content), save=False)
        instance.save()
        return instance

    def _create_import_record(self, request, model, action_type, column_titles):
        from django.apps import apps
        from django.utils import timezone as dj_timezone

        import_record_model = apps.get_model("system", "ImportRecord")
        name = "import_{}".format(dj_timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S"))
        return import_record_model.objects.create(
            name=name,
            module=str(model._meta.verbose_name),
            path=request.path,
            action=action_type,
            params={"action": action_type, "column_titles": column_titles},
            # 显式赋值消除 threadlocal 注入的时序依赖（OwnerFilter 依赖 creator）
            creator=request.user if getattr(request.user, "pk", None) else None,
        )

    @extend_schema(
        request=OpenApiRequest(build_basic_type(OpenApiTypes.BINARY)),
        responses=get_default_response_schema(
            {
                "total": build_basic_type(OpenApiTypes.NUMBER),
                "valid_count": build_basic_type(OpenApiTypes.NUMBER),
                "invalid_count": build_basic_type(OpenApiTypes.NUMBER),
                "errors_truncated": build_basic_type(OpenApiTypes.BOOL),
                "errors": build_object_type(
                    properties={
                        "row": build_basic_type(OpenApiTypes.NUMBER),
                        "field": build_basic_type(OpenApiTypes.STR),
                        "message": build_basic_type(OpenApiTypes.STR),
                    }
                ),
            }
        ),
    )
    @action(methods=["post"], detail=False, url_path="import-validate")
    def import_validate(self, request, *args, **kwargs):
        """导入前校验{cls}数据（逐行校验不落库，返回字段级错误定位）"""
        from common.core.config import SysConfig

        rows, _column_titles = self._get_rows_and_titles(request)
        errors, limit = [], SysConfig.IMPORT_VALIDATE_ERROR_LIMIT
        invalid_count = 0
        for idx, row in enumerate(rows, start=1):
            serializer = self.get_serializer(data=row)
            if not serializer.is_valid():
                invalid_count += 1
                if len(errors) < limit:
                    # 字段级展开：{row, field, message}，前端可精确定位到具体字段
                    errors.extend(_flatten_row_errors(idx, serializer.errors, limit - len(errors)))
        return ApiResponse(
            data={
                "total": len(rows),
                "valid_count": len(rows) - invalid_count,
                "invalid_count": invalid_count,
                "errors_truncated": invalid_count > 0 and len(errors) >= limit,
                "errors": errors,
            }
        )

    @extend_schema(
        request=OpenApiRequest(build_basic_type(OpenApiTypes.BINARY)),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="import-async")
    def import_async(self, request, *args, **kwargs):
        """异步导入{cls}数据（大数据量，进度与错误报告在下载中心获取）"""
        from django.db import transaction
        from django.utils.module_loading import import_string

        action_type = request.query_params.get("action") or "create"
        if action_type not in ("create", "update"):
            return ApiResponse(code=1001, detail=_("Operation failed. Abnormal data"))
        rows, column_titles = self._get_rows_and_titles(request)
        if not rows:
            return ApiResponse(code=1001, detail=_("Operation failed. Abnormal data"))
        limit_tip = self._check_running_limit(request)
        if limit_tip:
            return ApiResponse(code=1001, detail=limit_tip)
        model, view_path, user_pk = self._import_context(request)
        record = self._create_import_record(request, model, action_type, column_titles)
        # 行数据落 UploadFile(is_tmp=True)：任务内直接读 JSON，不重复解析原文件
        record.source_file = self._save_rows_file(rows, request.user, f"{record.pk}_rows.json")
        record.save(update_fields=["source_file", "updated_time"])
        args = [str(record.pk), view_path, user_pk]
        task = import_string("system.tasks.async_import_data_task")
        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            # 测试/E2E：send_task/apply_async 在 eager 下不执行，改 apply 同步跑完
            task.apply(args=args, task_id=str(record.pk))
        else:
            transaction.on_commit(lambda: task.apply_async(args=args, task_id=str(record.pk)))
        return ApiResponse(
            data={"record_id": str(record.pk), "task_id": str(record.pk)},
            detail=_("Import task submitted"),
        )


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
        # Deprecated：ignore_error=true 会静默丢弃失败行（无任何痕迹），仅为兼容保留；
        # 需要失败行定位/错误报告请改走 import-validate + import-async（异步导入 2.0）
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
