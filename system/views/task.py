#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""定时任务管理（django_celery_beat 业务侧 CRUD）。

提供周期任务 / crontab 表达式 / 固定间隔三类资源的管理接口，
替代此前仅能经 Django Admin 兜底管理的方式。启停变更由 beat 的
DatabaseScheduler 在 --max-interval（启动参数默认 60s）内感知生效。

数据权限沿用全局默认（未配置 DataPermission 规则的非超管不可见，默认拒绝），
按钮/菜单权限经菜单管理按 list:create:retrieve:partialUpdate:destroy:enable 配置。

序列化器已拆分至 system.serializers.task；本模块仅保留 Filter/ViewSet/action。
"""

import json
import os

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.decorators import action

from common.celery.utils import CELERY_LOG_MAGIC_MARK, get_celery_task_log_path
from common.core.modelset import ListDeleteModelSet, BaseModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from server.celery import app
from system.models.task import TaskExecution
from system.serializers.task import (
    CrontabScheduleSerializer,
    IntervalScheduleSerializer,
    PeriodicTaskSerializer,
    TaskExecutionSerializer,
)


class TaskExecutionFilter(filters.FilterSet):
    """执行历史过滤：任务名模糊 + 状态/关联任务/触发人精确 + 时间范围。"""

    name = filters.CharFilter(field_name="name", lookup_expr="icontains")
    created_time = filters.DateTimeFromToRangeFilter()

    class Meta:
        model = TaskExecution
        fields = ["name", "status", "periodic_task", "creator", "created_time"]


class TaskExecutionViewSet(ListDeleteModelSet):
    """任务执行历史"""

    queryset = TaskExecution.objects.all()
    serializer_class = TaskExecutionSerializer
    filterset_class = TaskExecutionFilter
    ordering = ["-created_time"]
    ordering_fields = ["created_time", "date_start", "date_finished"]

    LOG_READ_CHUNK = 64 * 1024

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
        offset = max(0, int(request.query_params.get("offset") or 0))
        path = get_celery_task_log_path(str(execution.pk))
        if not os.path.exists(path):
            return ApiResponse(
                data={
                    "offset": 0,
                    "finished": execution.date_finished is not None,
                    "content": "",
                }
            )
        size = os.path.getsize(path)
        offset = min(offset, size)
        with open(path, "rb") as fp:
            fp.seek(offset)
            chunk = fp.read(self.LOG_READ_CHUNK)
        next_offset = offset + len(chunk)
        finished = CELERY_LOG_MAGIC_MARK in chunk
        if finished:
            # 结束标记是落盘控制符，不能作为日志内容返回
            chunk = chunk.replace(CELERY_LOG_MAGIC_MARK, b"")
        return ApiResponse(
            data={
                "offset": next_offset,
                "finished": finished,
                "content": chunk.decode("utf-8", errors="replace"),
            }
        )


class PeriodicTaskFilter(filters.FilterSet):
    """PeriodicTask 无 creator/dept 等审计字段，不继承 BaseFilterSet（其声明的过滤器引用不存在的字段）。

    声明式过滤器必须同步列入 Meta.fields，search-fields 元数据才会计入（get_fields 仅取 Meta.fields）。
    """

    name = filters.CharFilter(field_name="name", lookup_expr="icontains")
    task = filters.CharFilter(field_name="task", lookup_expr="icontains")

    class Meta:
        model = PeriodicTask
        fields = ["name", "task", "enabled", "one_off", "queue"]


class CrontabScheduleFilter(filters.FilterSet):
    class Meta:
        model = CrontabSchedule
        fields = ["minute", "hour", "day_of_week", "month_of_year"]


class IntervalScheduleFilter(filters.FilterSet):
    class Meta:
        model = IntervalSchedule
        fields = ["every", "period"]


class CrontabScheduleViewSet(BaseModelSet):
    """crontab 表达式管理"""

    queryset = CrontabSchedule.objects.all().order_by("minute", "hour", "day_of_week", "month_of_year")
    serializer_class = CrontabScheduleSerializer
    filterset_class = CrontabScheduleFilter
    ordering = ["id"]
    ordering_fields = ["id"]


class IntervalScheduleViewSet(BaseModelSet):
    """固定间隔调度管理"""

    queryset = IntervalSchedule.objects.all().order_by("every", "period")
    serializer_class = IntervalScheduleSerializer
    filterset_class = IntervalScheduleFilter
    ordering = ["id"]
    ordering_fields = ["id"]


def _dispatch_periodic_run(instance):
    """为周期任务派发一次立即执行，返回新建的 TaskExecution。

    Raises:
        ValueError: 任务未注册或 args/kwargs 不是合法 JSON。
    """
    if instance.task not in app.tasks:
        # web 进程不启动 worker，任务模块（autodiscover）按需懒加载注册
        app.autodiscover_tasks(force=True)
    if instance.task not in app.tasks:
        raise ValueError(_('Task "{}" is not registered').format(instance.task))
    try:
        args = json.loads(instance.args or "[]")
        kwargs = json.loads(instance.kwargs or "{}")
    except (json.JSONDecodeError, TypeError):
        raise ValueError(_("Task arguments are not valid JSON"))
    execution = TaskExecution.objects.create(
        name=instance.task,
        periodic_task=instance,
        args=args,
        kwargs=kwargs,
    )
    # on_commit 保证记录先落库，publisher 进程的 after_task_publish 才能命中既有记录
    transaction.on_commit(
        lambda: app.send_task(
            instance.task,
            args=args,
            kwargs=kwargs,
            task_id=str(execution.pk),
            headers={"periodic_task_name": instance.name},
        )
    )
    return execution


class PeriodicTaskViewSet(BaseModelSet):
    """周期任务管理"""

    queryset = PeriodicTask.objects.all().order_by("name")
    serializer_class = PeriodicTaskSerializer
    filterset_class = PeriodicTaskFilter
    ordering = ["name"]
    ordering_fields = ["name", "enabled", "date_changed"]

    @extend_schema(
        request=build_object_type(properties={"enabled": build_basic_type(OpenApiTypes.BOOL)}),
        responses=get_default_response_schema(),
    )
    @action(methods=["patch"], detail=True)
    def enable(self, request, *args, **kwargs):
        """启用或停用{cls}任务"""
        instance = self.get_object()
        enabled = request.data.get("enabled")
        instance.enabled = (not instance.enabled) if enabled is None else bool(enabled)
        instance.save()
        return ApiResponse(data={"pk": instance.pk, "enabled": instance.enabled})

    @extend_schema(
        request=None,
        responses=get_default_response_schema(),
    )
    @action(methods=["get"], detail=False, url_path="registered")
    def registered(self, request, *args, **kwargs):
        """已注册任务列表"""
        # web 进程不启动 worker，任务模块（autodiscover）按需懒加载注册；
        # 进程内可能已零散注册部分业务任务但缺 system.tasks 等，故每次强制补齐
        app.autodiscover_tasks(force=True)
        items = []
        for name, task in sorted(app.tasks.items()):
            if name.startswith("celery."):
                continue
            verbose_name = getattr(task, "verbose_name", None) or ""
            items.append({"name": name, "verbose_name": str(verbose_name)})
        return ApiResponse(data=items)

    @extend_schema(
        request=None,
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True, url_path="run")
    def run(self, request, *args, **kwargs):
        """立即执行一次{cls}任务"""
        try:
            execution = _dispatch_periodic_run(self.get_object())
        except ValueError as exc:
            return ApiResponse(code=400, detail=str(exc))
        return ApiResponse(data={"task_id": str(execution.pk)})

    @extend_schema(
        request=OpenApiRequest(build_array_type(build_basic_type(OpenApiTypes.STR))),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-run")
    def batch_run(self, request, *args, **kwargs):
        """批量立即执行{cls}任务"""
        success, failed = 0, []
        queryset = self.filter_queryset(self.get_queryset()).filter(pk__in=request.data)
        for instance in queryset:
            try:
                _dispatch_periodic_run(instance)
            except ValueError as exc:
                failed.append({"pk": str(instance.pk), "name": instance.name, "detail": str(exc)})
            else:
                success += 1
        return ApiResponse(
            data={"success": success, "failed": failed},
            detail=_("Batch execution submitted: {} success, {} failed").format(success, len(failed)),
        )
