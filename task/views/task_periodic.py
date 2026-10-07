#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""定时任务视图：周期任务 / Cron 与间隔计划 CRUD + 运行/克隆/批量启停（自 task.py 拆分，URL 路径与权限点不变）。"""

import json
import threading

from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError

from common.core.modelset import BaseModelSet, BatchPartialUpdateAction
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from server.celery import app
from task.models.task import TaskExecution
from task.serializers.task import (
    CrontabScheduleSerializer,
    IntervalScheduleSerializer,
    PeriodicTaskSerializer,
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


# 删除拒绝时受影响任务名的展示上限：超出部分以计数收尾，避免超长调度名撑爆响应
SCHEDULE_REF_PREVIEW_LIMIT = 5


class ScheduleDeleteGuardMixin:
    """调度（crontab / interval）删除引用预检。

    django_celery_beat 的 ``PeriodicTask.crontab/interval`` 均为
    ``on_delete=CASCADE``：直接删除调度会把引用它的周期任务**静默级联删除**
    （beat 停跑且无逐任务确认）。删除前做引用预检：

    - 单删（destroy → perform_destroy）：被引用即抛 ValidationError（DRF 400
      可读拒绝，并列出受影响任务名）；
    - 批删（batch-destroy）：覆写 ``_needs_rowwise_delete`` 强制逐行分支——
      否则调度模型非软删会走整批 ``delete()``，级联绕过预检；被引用项进
      ``data.failures`` 明细（原因同单删文案），未引用项正常删除。
    """

    # PeriodicTask 关联本调度模型的外键字段名（子类声明：crontab / interval）
    schedule_field = ""

    def perform_destroy(self, instance):
        self._ensure_schedule_unreferenced(instance)
        # 必须回传删除结果：batch_destroy 逐行分支以返回值区分 success / failures，
        # 丢弃返回值会把已成功删除的调度误报为「未删除」（mixin 模式，见 file_access 同款）
        return super().perform_destroy(instance)  # type: ignore[misc]  # 宿主 ViewSet 提供同名方法

    def _needs_rowwise_delete(self):
        return True

    def _ensure_schedule_unreferenced(self, instance) -> None:
        if not self.schedule_field:  # pragma: no cover - 子类未声明时 fail-closed 不放行
            raise ValidationError(_("Schedule reference guard is not configured"))
        queryset = PeriodicTask.objects.filter(**{self.schedule_field: instance})
        total = queryset.count()
        if not total:
            return
        names = list(queryset.order_by("name").values_list("name", flat=True)[:SCHEDULE_REF_PREVIEW_LIMIT])
        preview = ", ".join(names)
        if total > len(names):
            preview = _("%(names)s and %(count)s more") % {"names": preview, "count": total - len(names)}
        raise ValidationError(
            _("Cannot delete: %(count)s periodic task(s) reference this schedule: %(tasks)s")
            % {"count": total, "tasks": preview}
        )


class CrontabScheduleViewSet(ScheduleDeleteGuardMixin, BaseModelSet):
    """crontab 表达式管理"""

    schedule_field = "crontab"
    queryset = CrontabSchedule.objects.all()
    serializer_class = CrontabScheduleSerializer
    filterset_class = CrontabScheduleFilter
    ordering = ["id"]
    ordering_fields = ["id"]


class IntervalScheduleViewSet(ScheduleDeleteGuardMixin, BaseModelSet):
    """固定间隔调度管理"""

    schedule_field = "interval"
    queryset = IntervalSchedule.objects.all()
    serializer_class = IntervalScheduleSerializer
    filterset_class = IntervalScheduleFilter
    ordering = ["id"]
    ordering_fields = ["id"]


# 全量 autodiscover 要遍历全部 INSTALLED_APPS 并 import 各 app 的 tasks 模块，
# 开销大，web 进程（不启动 worker，任务模块按需懒加载）只在首个触发点做一次，
# 后续请求直接复用 app.tasks。新装 app / 新增任务模块本就要求进程重启才生效，
# 绕过进程内缓存的场景以 force=True 显式重扫。
_autodiscover_lock = threading.Lock()
_autodiscovered = False


def ensure_tasks_registered(force: bool = False) -> None:
    """确保业务任务完成一次全量注册（进程内一次，并发下加锁防重复扫描）。"""
    global _autodiscovered
    if _autodiscovered and not force:
        return
    with _autodiscover_lock:
        if _autodiscovered and not force:
            return
        # 进程内可能已零散注册部分业务任务但缺 system.tasks 等，故强制补齐；
        # 扫描抛错时不置位，下次请求重试（与逐请求扫描的重试语义一致）
        app.autodiscover_tasks(force=True)
        _autodiscovered = True


def _dispatch_periodic_run(instance):
    """为周期任务派发一次立即执行，返回新建的 TaskExecution。

    Raises:
        ValueError: 任务未注册、不在可手动执行白名单，或 args/kwargs 不是合法 JSON。
    """
    from task.utils.task_whitelist import is_task_runnable

    if not is_task_runnable(instance.task):
        # 白名单在执行侧再拦一道：只挡写入不挡执行，存量任务可绕过
        raise ValueError(
            _('Task "{}" is not allowed for manual execution (not in the runnable whitelist)').format(instance.task)
        )
    if instance.task not in app.tasks:
        # 未注册时补一次全量注册（进程内只扫一次，不随未注册任务的重试反复全量 import）
        ensure_tasks_registered()
    if instance.task not in app.tasks:
        raise ValueError(_('Task "{}" is not registered').format(instance.task))
    try:
        args = json.loads(instance.args or "[]")
        kwargs = json.loads(instance.kwargs or "{}")
    except (json.JSONDecodeError, TypeError):
        raise ValueError(_("Task arguments are not valid JSON")) from None
    execution = TaskExecution.objects.create(
        name=instance.task,
        periodic_task=instance,
        args=args,
        kwargs=kwargs,
    )

    # on_commit 保证记录先落库，publisher 进程的 after_task_publish 才能命中既有记录
    def _dispatch():
        app.send_task(
            instance.task,
            args=args,
            kwargs=kwargs,
            task_id=str(execution.pk),
            headers={"periodic_task_name": instance.name},
        )

    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        # 测试/E2E：task_always_eager 对 send_task 无效（AlwaysEagerIgnored，
        # 消息投 memory broker 无人消费，执行记录永远停在 PENDING）；改走
        # apply 同步执行，触发 prerun/postrun 信号完成状态流转
        app.tasks[instance.task].apply(
            args=args,
            kwargs=kwargs,
            task_id=str(execution.pk),
            headers={"periodic_task_name": instance.name},
        )
    else:
        transaction.on_commit(_dispatch)
    return execution


def _clean_pks(pks) -> list:
    """清洗主键列表：PeriodicTask 主键为整型，非法值直接忽略而非查询报错"""
    valid = []
    for pk in pks or []:
        try:
            valid.append(int(pk))
        except (TypeError, ValueError):
            continue
    return valid


def _next_available_clone_name(source_name: str) -> str:
    """取克隆名的第一个可用候选：一次查询取回同前缀既有名，内存推导后缀。

    命名格式与逐次探测时保持一致：首选「{source_name}-copy」，被占用则依次
    尝试「{source_name}-copy-2」「-copy-3」……（不存在「-copy-1」形态）。
    同前缀但非「-{数字}」后缀的名字（如手工改名的「-copy-备份」）不参与
    后缀占位；「-copy-1」也不阻塞首选名。同名密集时避免逐候选一次 EXISTS。
    """
    base_name = f"{source_name}-copy"
    base_taken = False
    taken_suffixes = set()
    for existing in PeriodicTask.objects.filter(name__startswith=base_name).values_list("name", flat=True):
        suffix = existing[len(base_name) :]
        if suffix == "":
            base_taken = True
        elif suffix.startswith("-") and suffix[1:].isdecimal():
            # isdecimal 而非 isdigit：上标数字（如「²」）.isdigit 为真但 int() 抛错
            taken_suffixes.add(int(suffix[1:]))
    if not base_taken:
        return base_name
    index = 2
    while index in taken_suffixes:
        index += 1
    return f"{base_name}-{index}"


def _clone_periodic_task(instance: PeriodicTask) -> PeriodicTask:
    """克隆周期任务：复制调度与参数，生成唯一名称，默认停用（避免克隆即执行）"""
    name = _next_available_clone_name(instance.name)
    clone = PeriodicTask.objects.get(pk=instance.pk)
    clone.pk = None
    clone.name = name
    clone.enabled = False
    clone.last_run_at = None
    clone.total_run_count = 0
    clone.save()  # 触发 django_celery_beat 信号，beat 感知新任务
    return clone


class PeriodicTaskViewSet(BatchPartialUpdateAction, BaseModelSet):
    """周期任务管理"""

    queryset = PeriodicTask.objects.all().order_by("name")
    serializer_class = PeriodicTaskSerializer
    filterset_class = PeriodicTaskFilter
    ordering = ["name"]
    ordering_fields = ["name", "enabled", "date_changed"]
    # 批量更新白名单：批量启停用
    batch_update_fields = ("enabled",)

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
        parameters=[
            OpenApiParameter(
                name="refresh",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                description="强制重新扫描任务模块（默认复用进程内已注册任务）",
            )
        ],
        request=None,
        responses=get_default_response_schema(),
    )
    @action(methods=["get"], detail=False, url_path="registered")
    def registered(self, request, *args, **kwargs):
        """已注册任务列表（附 runnable 标记：是否在可手动执行白名单内）"""
        # 全量 autodiscover 开销大，进程内只做一次；新装 app 后需立即可见时
        # 带 refresh=1 强制重扫，缺省行为与既往接口保持兼容
        refresh = str(request.query_params.get("refresh", "")).strip().lower() in ("1", "true", "yes")
        ensure_tasks_registered(force=refresh)
        from task.utils.task_whitelist import is_task_runnable

        items = []
        for name, task in sorted(app.tasks.items()):
            if name.startswith("celery."):
                continue
            verbose_name = getattr(task, "verbose_name", None) or ""
            items.append({"name": name, "verbose_name": str(verbose_name), "runnable": is_task_runnable(name)})
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
        request=OpenApiRequest(build_array_type(build_basic_type(OpenApiTypes.STR) or {})),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-run")
    def batch_run(self, request, *args, **kwargs):
        """批量立即执行{cls}任务"""
        success, failed = 0, []
        queryset = self.filter_queryset(self.get_queryset()).filter(pk__in=_clean_pks(request.data))
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

    @extend_schema(
        request=build_object_type(
            properties={
                "pks": build_array_type(build_basic_type(OpenApiTypes.STR) or {}),
                "enabled": build_basic_type(OpenApiTypes.BOOL),
            }
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-enable")
    def batch_enable(self, request, *args, **kwargs):
        """批量启用或停用{cls}任务

        body: {"pks": [...], "enabled": bool}；enabled 省略时按各任务当前状态取反。
        逐个 save 而非 queryset.update：触发 django_celery_beat 信号让 beat 感知变更。
        非法主键、未命中（不存在或被过滤）与保存失败的项进入 failed 明细，
        结构与 batch-run 的失败口径一致（pk/name + 原因）。
        """
        pks = request.data.get("pks") or []
        enabled = request.data.get("enabled")
        success = 0
        failed = []
        # 主键类型安全规范化：非法值不静默丢弃，归入失败明细（与批量删除/更新口径一致）
        valid_pks = []
        for raw_pk in pks:
            try:
                valid_pks.append(int(raw_pk))
            except (TypeError, ValueError):
                failed.append({"pk": str(raw_pk), "name": None, "detail": str(_("Not found or no permission"))})
        queryset = self.filter_queryset(self.get_queryset()).filter(pk__in=valid_pks)
        matched_pks = set()
        for instance in queryset:
            matched_pks.add(instance.pk)
            try:
                instance.enabled = (not instance.enabled) if enabled is None else bool(enabled)
                instance.save()
                success += 1
            except Exception as exc:  # noqa: BLE001 单项失败不影响其余项（部分成功语义）
                failed.append({"pk": str(instance.pk), "name": instance.name, "detail": str(exc)})
        # 请求里有但查询未命中的主键同样如实进失败明细，不再恒返回 failed: []
        for valid_pk in valid_pks:
            if valid_pk not in matched_pks:
                failed.append({"pk": str(valid_pk), "name": None, "detail": str(_("Not found or no permission"))})
        return ApiResponse(
            data={"success": success, "failed": failed},
            detail=_("Batch update submitted: {} success, {} failed").format(success, len(failed)),
        )

    @extend_schema(
        request=None,
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True, url_path="clone")
    def clone(self, request, *args, **kwargs):
        """克隆{cls}任务（复制计划与参数，默认停用，避免克隆即执行）"""
        instance = self.get_object()
        clone = _clone_periodic_task(instance)
        return ApiResponse(
            data={"pk": str(clone.pk), "name": clone.name},
            detail=_("Task cloned: {}").format(clone.name),
        )
