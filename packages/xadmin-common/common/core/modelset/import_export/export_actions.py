#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""导入导出：文件导出（export-data / export-async）。"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiRequest, OpenApiResponse, extend_schema

from common.core.modelset.crud import ListAction
from common.core.permission_meta import parent_fallback_action
from common.core.response import ApiResponse
from common.core.throttle import ExportImportThrottleMixin
from common.drf.renders.csv import CSVFileRenderer
from common.drf.renders.excel import ExcelFileRenderer
from common.settings_contract import kernel_setting
from common.swagger.utils import get_default_response_schema


class OnlyExportDataAction(ExportImportThrottleMixin, ListAction):
    # 敏感读取审计：同步导出把全表数据渲染成文件外发，单列落操作日志
    # （API_LOG_METHODS 默认不含 GET，由中间件按本声明单独放行）
    SENSITIVE_GET_ACTIONS: tuple[str, ...] = ("export_data",)
    # 专用限流：同步导出与异步提交都按 export_import 档收敛
    export_import_actions: tuple[str, ...] = ("export_data", "export_async")

    @extend_schema(
        parameters=[
            OpenApiParameter(name="type", required=True, enum=["xlsx", "csv"]),
        ],
        responses={200: OpenApiResponse(build_basic_type(OpenApiTypes.BINARY))},
    )
    @parent_fallback_action(methods=["get"], detail=False, url_path="export-data")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def export_data(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """导出{cls}数据

        type=csv|xlsx，缺省 xlsx

        走流式渲染（见 import_export/streaming.py）：逐行序列化 + 逐行落临时文件 +
        文件响应，峰值内存不随导出行数增长；不再经 DRF 把全量列表与整本工作簿同时
        驻留内存。旧渲染器仍服务导入模板（``template=import``，含下拉校验）。
        """
        from common.core.modelset.import_export.streaming import stream_export_response

        file_format = request.query_params.get("type", "xlsx")
        self.format_kwarg = file_format
        request.no_cache = True  # 防止自定义缓存数据
        # 保持渲染器声明（内容协商与 OpenAPI 仍按 xlsx 声明二进制响应）
        self.renderer_classes = [ExcelFileRenderer, CSVFileRenderer]
        renderer = CSVFileRenderer() if file_format == "csv" else ExcelFileRenderer()
        request.accepted_renderer = renderer
        request.accepted_media_type = renderer.media_type
        if request.query_params.get("template", "export") != "export":
            # 导入/更新模板保留旧链路：需要表头帮助行与 Excel 数据校验（write_only 不支持）
            return self.list(request, *args, **kwargs)
        return stream_export_response(self, request, file_format)

    @extend_schema(
        request=OpenApiRequest(build_object_type(properties={"type": build_basic_type(OpenApiTypes.STR)})),
        responses=get_default_response_schema(),
    )
    @parent_fallback_action(methods=["post"], detail=False, url_path="export-async")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def export_async(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """异步导出{cls}数据"""
        from django.apps import apps
        from django.db import transaction
        from django.utils import timezone as dj_timezone
        from django.utils.module_loading import import_string

        from common.core.config import SysConfig, get_personal_int_config

        params = dict(request.data) if isinstance(request.data, dict) else {}
        for key, value in request.query_params.items():
            params.setdefault(key, value)
        file_format = params.get("type") or "xlsx"
        model = self.get_queryset().model
        name = "{}_{}".format(model._meta.model_name, dj_timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S"))
        # 跨 app 惰性取模型/任务：common 层不直接依赖 system（契约层约束，见 check_cross_app_imports）
        export_record_model = apps.get_model("task", "ExportRecord")
        # 同用户并发上限：导出是最重的后台任务，防止重复点击/脚本刷爆 worker。
        # 真实个人行优先，未设置回退系统级
        max_running = get_personal_int_config(
            request.user, "EXPORT_ASYNC_MAX_RUNNING", SysConfig.EXPORT_ASYNC_MAX_RUNNING
        )
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
        task_args = [
            str(record.pk),
            f"{self.__class__.__module__}.{self.__class__.__name__}",
            params,
            getattr(request.user, "pk", None),
        ]
        task = import_string("system.tasks.async_export_data_task")
        if kernel_setting("CELERY_TASK_ALWAYS_EAGER"):
            # 测试/E2E：send_task/apply_async 在 eager 下不执行，改 apply 同步跑完
            task.apply(args=task_args, task_id=str(record.pk))
        else:
            transaction.on_commit(lambda: task.apply_async(args=task_args, task_id=str(record.pk)))
        return ApiResponse(
            data={"record_id": str(record.pk), "task_id": str(record.pk)},
            detail=_("Export task submitted"),
        )
