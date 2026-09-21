#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""导入导出：文件导出（export-data / export-async）。"""

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiRequest, OpenApiResponse, extend_schema
from rest_framework.decorators import action

from common.core.modelset.crud import ListAction
from common.core.response import ApiResponse
from common.drf.renders.csv import CSVFileRenderer
from common.drf.renders.excel import ExcelFileRenderer
from common.swagger.utils import get_default_response_schema


class OnlyExportDataAction(ListAction):
    @extend_schema(
        parameters=[
            OpenApiParameter(name="type", required=True, enum=["xlsx", "csv"]),
        ],
        responses={200: OpenApiResponse(build_basic_type(OpenApiTypes.BINARY))},
    )
    @action(methods=["get"], detail=False, url_path="export-data")
    def export_data(self, request, *args, **kwargs):
        """导出{cls}数据（type=csv|xlsx，缺省 xlsx）"""
        file_format = request.query_params.get("type", "xlsx")
        self.format_kwarg = file_format
        request.no_cache = True  # 防止自定义缓存数据
        self.renderer_classes = [ExcelFileRenderer, CSVFileRenderer]
        # 显式绑定渲染器：DRF 内容协商按 Accept 列表挑渲染器，浏览器/axios 默认
        # `Accept: application/json` 会一路落到 renderers[0]（xlsx），**type=csv 被忽略**
        # （历史缺陷：全站选 CSV 导出的文件实际是 xlsx）。这里按 type 直接指定，
        # 并把 accepted_renderer 交给 finalize_response（非空即不再协商）。
        renderer = CSVFileRenderer() if file_format == "csv" else ExcelFileRenderer()
        request.accepted_renderer = renderer
        request.accepted_media_type = renderer.media_type
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

        from common.core.config import SysConfig, get_personal_int_config

        params = dict(request.data) if isinstance(request.data, dict) else {}
        for key, value in request.query_params.items():
            params.setdefault(key, value)
        file_format = params.get("type") or "xlsx"
        model = self.get_queryset().model
        name = "{}_{}".format(model._meta.model_name, dj_timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S"))
        # 跨 app 惰性取模型/任务：common 层不直接依赖 system（契约层约束，见 check_cross_app_imports）
        export_record_model = apps.get_model("system", "ExportRecord")
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
