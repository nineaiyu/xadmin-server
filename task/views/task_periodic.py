#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""定时任务视图：周期任务 / Cron 与间隔计划 CRUD + 运行/克隆/批量启停（自 task.py 拆分，URL 路径与权限点不变）。"""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError

from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import parse_bool
from server.celery import app
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
    crontab = filters.NumberFilter(field_name="crontab_id")
    interval = filters.NumberFilter(field_name="interval_id")

    class Meta:
        model = PeriodicTask
        fields = ["name", "task", "enabled", "one_off", "queue", "crontab", "interval"]


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

    def destroy(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """单删被拒绝时返回结构化受影响任务，供前端弹窗展示并可跳转筛选。"""
        instance = self.get_object()  # type: ignore[attr-defined]  # 宿主 ViewSet 提供
        references = self._referencing_tasks(instance)
        if references["total"]:
            return ApiResponse(
                code=1001,
                status=400,
                detail=self._reference_message(references),
                data=references,
            )
        return super().destroy(request, *args, **kwargs)  # type: ignore[misc]  # 宿主 ViewSet 提供同名方法

    def perform_destroy(self, instance: Any) -> Any:
        self._ensure_schedule_unreferenced(instance)
        # 必须回传删除结果：batch_destroy 逐行分支以返回值区分 success / failures，
        # 丢弃返回值会把已成功删除的调度误报为「未删除」（mixin 模式，见 file_access 同款）
        return super().perform_destroy(instance)  # type: ignore[misc]  # 宿主 ViewSet 提供同名方法

    def _needs_rowwise_delete(self) -> Any:
        return True

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="references")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def references(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """引用当前调度的周期任务清单（删除前的持久入口）。"""
        return ApiResponse(data=self._referencing_tasks(self.get_object()))  # type: ignore[attr-defined]  # 宿主 ViewSet 提供

    def _referencing_tasks(self, instance: Any) -> dict[str, Any]:
        if not self.schedule_field:  # pragma: no cover - 子类未声明时 fail-closed 不放行
            raise ValidationError(_("Schedule reference guard is not configured"))
        queryset = PeriodicTask.objects.filter(**{self.schedule_field: instance}).order_by("name")
        total = queryset.count()
        tasks = list(queryset.values("pk", "name")[:50])
        return {
            "total": total,
            "tasks": [{"pk": str(item["pk"]), "name": item["name"]} for item in tasks],
            "truncated": total > len(tasks),
        }

    def _reference_message(self, references: dict[str, Any]) -> str:
        total = references["total"]
        names = ", ".join(item["name"] for item in references["tasks"][:SCHEDULE_REF_PREVIEW_LIMIT])
        if total > SCHEDULE_REF_PREVIEW_LIMIT:
            names = _("%(names)s and %(count)s more") % {
                "names": names,
                "count": total - SCHEDULE_REF_PREVIEW_LIMIT,
            }
        return str(
            _("Cannot delete: %(count)s periodic task(s) reference this schedule: %(tasks)s")
            % {"count": total, "tasks": names}
        )

    def _ensure_schedule_unreferenced(self, instance: Any) -> None:
        references = self._referencing_tasks(instance)
        if not references["total"]:
            return
        raise ValidationError(self._reference_message(references))


class CrontabScheduleViewSet(ScheduleDeleteGuardMixin, BaseModelSet):
    """crontab 表达式管理"""

    schedule_field = "crontab"
    queryset = CrontabSchedule.objects.all()
    serializer_class = CrontabScheduleSerializer
    filterset_class = CrontabScheduleFilter
    ordering = ["id"]
    ordering_fields = ["id"]

    @extend_schema(
        request=CrontabScheduleSerializer,
        responses=get_default_response_schema(
            {
                "expression": build_basic_type(OpenApiTypes.STR),
                "timezone": build_basic_type(OpenApiTypes.STR),
                "next_runs": build_array_type(build_basic_type(OpenApiTypes.STR) or {}),
            }
        ),
    )
    @action(methods=["post"], detail=False, url_path="preview")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def preview(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """预览未来五次触发时间

        按当前五字段计算，并按声明时区换算。
        """
        serializer = CrontabScheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        schedule = CrontabSchedule(**serializer.validated_data)
        timezone_name = str(getattr(schedule.timezone, "key", schedule.timezone) or settings.CELERY_TIMEZONE)
        try:
            tzinfo = ZoneInfo(timezone_name)
        except Exception:  # noqa: BLE001 时区已在序列化器校验，防御第三方数据异常
            return ApiResponse(code=1001, detail=_("Unknown timezone: {}").format(timezone_name))
        fields = ("minute", "hour", "day_of_month", "month_of_year", "day_of_week")
        expression = " ".join(str(getattr(schedule, field)) for field in fields)
        try:
            iterator = croniter(expression, datetime.now(tzinfo))
            next_runs = [iterator.get_next(datetime).isoformat() for _ in range(5)]
        except (ValueError, KeyError) as exc:
            return ApiResponse(code=1001, detail=_("Invalid crontab expression: {}").format(exc))
        return ApiResponse(data={"expression": expression, "timezone": timezone_name, "next_runs": next_runs})


class IntervalScheduleViewSet(ScheduleDeleteGuardMixin, BaseModelSet):
    """固定间隔调度管理"""

    schedule_field = "interval"
    queryset = IntervalSchedule.objects.all()
    serializer_class = IntervalScheduleSerializer
    filterset_class = IntervalScheduleFilter
    ordering = ["id"]
    ordering_fields = ["id"]

    def get_queryset(self) -> Any:
        from django.db.models import Count

        return super().get_queryset().annotate(reference_count=Count("periodictask"))


# 全量 autodiscover 要遍历全部 INSTALLED_APPS 并 import 各 app 的 tasks 模块，
from task.services.periodic import (  # noqa: F401 显式再导出：视图/测试按原路径导入
    _clean_pks as _clean_pks,
)
from task.services.periodic import (
    _clone_periodic_task as _clone_periodic_task,
)
from task.services.periodic import (
    _dispatch_periodic_run as _dispatch_periodic_run,
)
from task.services.periodic import (
    _next_available_clone_name as _next_available_clone_name,
)
from task.services.periodic import (
    ensure_tasks_registered as ensure_tasks_registered,
)


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
    @action(methods=["patch"], detail=True)  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def enable(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """启用或停用{cls}任务"""
        instance = self.get_object()
        enabled = request.data.get("enabled")
        if enabled is not None:
            try:
                enabled = parse_bool(enabled)
            except ValueError:
                raise ValidationError({"enabled": _("Invalid boolean value")}) from None
        instance.enabled = (not instance.enabled) if enabled is None else enabled
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
    @action(methods=["get"], detail=False, url_path="registered")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def registered(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """已注册任务列表

        附 runnable 标记：是否在可手动执行白名单内
        """
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
    @action(methods=["post"], detail=True, url_path="run")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def run(self, request: Any, *args: Any, **kwargs: Any) -> Any:
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
    @action(methods=["post"], detail=False, url_path="batch-run")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def batch_run(self, request: Any, *args: Any, **kwargs: Any) -> Any:
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
            properties={"pks": build_array_type(build_basic_type(OpenApiTypes.STR) or {})},
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-enable")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def batch_enable(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """批量启用{cls}任务

        独立权限点，与批量停用分路由。
        """
        return self._batch_set_enabled(request, enabled=True)

    @extend_schema(
        request=build_object_type(
            properties={"pks": build_array_type(build_basic_type(OpenApiTypes.STR) or {})},
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-disable")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def batch_disable(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """批量停用{cls}任务

        独立权限点，与批量启用分路由。
        """
        return self._batch_set_enabled(request, enabled=False)

    def _batch_set_enabled(self, request: Any, enabled: bool) -> Any:
        pks = request.data.get("pks") or []
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
                instance.enabled = enabled
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
    @action(methods=["post"], detail=True, url_path="clone")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def clone(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """克隆{cls}任务

        复制计划与参数，默认停用，避免克隆即执行
        """
        instance = self.get_object()
        clone = _clone_periodic_task(instance)
        return ApiResponse(
            data={"pk": str(clone.pk), "name": clone.name},
            detail=_("Task cloned: {}").format(clone.name),
        )
