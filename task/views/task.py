#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""定时任务管理（django_celery_beat 业务侧 CRUD）。

提供周期任务 / crontab 表达式 / 固定间隔三类资源的管理接口，
替代此前仅能经 Django Admin 兜底管理的方式。启停变更由 beat 的
DatabaseScheduler 在 --max-interval（启动参数默认 60s）内感知生效。

数据权限沿用全局默认（未配置 DataPermission 规则的非超管不可见，默认拒绝），
按钮/菜单权限经菜单管理按 list:create:retrieve:partialUpdate:destroy:enable 配置。

序列化器已拆分至 task.serializers.task；本模块仅保留 Filter/ViewSet/action。
"""

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from common.core.filter import ControlledLookupFilterBackend
from common.core.modelset import ListDeleteModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from task.models.task import TaskExecution
from task.serializers.task import (
    TaskExecutionSerializer,
)
from task.utils.task_log import read_task_log_chunk
from task.views.admin.record_base import RecordStatsMixin


class TaskExecutionFilter(filters.FilterSet):
    """执行历史过滤：任务名模糊 + 状态/关联任务/触发人精确 + 记录类型 + 时间范围。"""

    # 记录类型：按产物表（导出/导入）是否存在同 pk 记录判定（与列表注解同源）
    # 声明在前 → 搜索区首个字段，收起态即可见（最常用的一维筛选）
    product_type = filters.ChoiceFilter(
        choices=[
            ("task", _("Task")),
            ("export", _("Export")),
            ("import", _("Import")),
        ],
        method="filter_product_type",
        label=_("Record type"),
    )
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")
    created_time = filters.DateTimeFromToRangeFilter()

    class Meta:
        model = TaskExecution
        # 顺序即搜索区字段顺序：记录类型放首位（最常用，收起态即可见）
        fields = ["product_type", "name", "status", "periodic_task", "creator", "created_time"]

    def filter_product_type(self, queryset, name, value):
        from task.models.export import ExportRecord
        from task.models.import_ import ImportRecord
        from task.utils.task_center import TYPE_EXPORT, TYPE_IMPORT

        exports = ExportRecord.objects.values("pk")
        imports = ImportRecord.objects.values("pk")
        if value == TYPE_EXPORT:
            return queryset.filter(pk__in=exports)
        if value == TYPE_IMPORT:
            return queryset.filter(pk__in=imports)
        # 非产物记录 = 两张产物表 pk 并集的补集：合并为单个 IN 子查询，
        # 避免逐表 anti-join（union 臂上以空 order_by 去掉模型默认排序）
        return queryset.exclude(pk__in=exports.order_by().union(imports.order_by()))


class TaskExecutionViewSet(RecordStatsMixin, ListDeleteModelSet):
    """任务日志：所有 celery 任务的执行记录（定时调度 + 即时执行 + 导出/导入/报表产物任务）。

    产物任务（导出/导入）与执行记录共用主键（pk = celery task_id），列表按 pk 子查询
    带出产物信息（类型 / 进度 / 阶段 / 错误 / 产物文件），因此一个任务只有一行，
    同时具备「查看日志 / 取消 / 重跑 / 下载产物 / 清理」的完整入口。
    """

    queryset = TaskExecution.objects.all()
    serializer_class = TaskExecutionSerializer
    filterset_class = TaskExecutionFilter
    ordering = ["-created_time"]
    ordering_fields = ["created_time", "date_start", "date_finished"]
    # 高级筛选：字段面 = TaskExecutionFilter 已声明字段（受控 lookup 透传）
    controlled_lookup = True
    extra_filter_class = [ControlledLookupFilterBackend]

    def get_queryset(self):
        """列表带出产物信息（其它动作保持原查询，避免注解影响统计与单条操作）。

        导出/导入记录与执行历史共用主键（pk = celery task_id），一行至多命中
        一张产物表：两表的展示字段合并为单个 JSON 注解（COALESCE 先导出后导入，
        取值顺序与逐字段 Coalesce 一致），行级相关子查询由每行 8 个收敛为
        COALESCE 短路求值的 2 个；产物文件有无仍按存在性 Case 判定——该写法
        语义是对两张产物表取或，无法并入单表 JSON，其子查询与行无关、整查询
        只求值一次，不构成行级放大。
        """
        queryset = super().get_queryset()
        if self.action != "list":
            return queryset
        from django.db.models import (
            BooleanField,
            Case,
            F,
            JSONField,
            OuterRef,
            Subquery,
            Value,
            When,
        )
        from django.db.models.functions import Coalesce, JSONObject

        from task.models.export import ExportRecord
        from task.models.import_ import ImportRecord
        from task.utils.task_center import TYPE_EXPORT, TYPE_IMPORT

        # 同主键探测至多命中一行，order_by() 去掉模型默认排序（省去每行探测的排序节点）
        export = (
            ExportRecord.objects.filter(pk=OuterRef("pk"))
            .annotate(
                product=JSONObject(
                    type=Value(TYPE_EXPORT), name=F("name"), progress=F("progress"), stage=F("stage"), error=F("error")
                )
            )
            .order_by()
            .values("product")
        )
        imported = (
            ImportRecord.objects.filter(pk=OuterRef("pk"))
            .annotate(
                product=JSONObject(
                    type=Value(TYPE_IMPORT), name=F("name"), progress=F("progress"), stage=F("stage"), error=F("error")
                )
            )
            .order_by()
            .values("product")
        )
        return queryset.annotate(
            product_data=Coalesce(
                Subquery(export, output_field=JSONField()),
                Subquery(imported, output_field=JSONField()),
                output_field=JSONField(),
            ),
            # 是否有产物文件：按存在性判定（导出看 file、导入看错误报告），
            # 不下发 UUID 本体——避免 sqlite 下 UUID 子查询的类型转换边界
            product_has_file=Case(
                When(pk__in=ExportRecord.objects.exclude(file__isnull=True).values("pk"), then=Value(True)),
                When(pk__in=ImportRecord.objects.exclude(error_report__isnull=True).values("pk"), then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            ),
        )

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
        """增量读取执行日志"""
        execution = self.get_object()
        data = read_task_log_chunk(
            execution.pk,
            offset=request.query_params.get("offset") or 0,
            finished_hint=execution.date_finished is not None,
        )
        return ApiResponse(data=data)


# 实现拆至 task.views.task_periodic：经模块级 __getattr__ 延迟再导出（保持调用面，避免循环导入）。
_MOVED_EXPORTS = (
    "CrontabScheduleFilter",
    "CrontabScheduleViewSet",
    "IntervalScheduleFilter",
    "IntervalScheduleViewSet",
    "PeriodicTaskFilter",
    "PeriodicTaskViewSet",
    "_clean_pks",
    "_clone_periodic_task",
    "_dispatch_periodic_run",
)


def __getattr__(name):
    if name in _MOVED_EXPORTS:
        from importlib import import_module

        return getattr(import_module("task.views.task_periodic"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
