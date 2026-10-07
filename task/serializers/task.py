#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""定时任务（django_celery_beat）序列化器。

periodic task / crontab / interval / 执行历史（TaskExecution）四类资源的
序列化与字段校验：调度关联字段经 DisplayRelatedField 输出 {pk,label}
供前端列表/下拉直接可读；args/kwargs JSON 与 crontab 表达式入库前校验。
"""

import json

from celery.schedules import crontab_parser
from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask
from rest_framework import serializers

from common.core.fields import DictChoiceField
from common.core.serializers import BaseModelSerializer, BasePrimaryKeyRelatedField
from task.models.task import TaskExecution
from task.utils.task_center import ACTIVE_STATUSES

# celery crontab_parser 各字段的取值跨度（min-max 由 parser 按 steps 推导）
_CRONTAB_STEPS = {
    "minute": 60,
    "hour": 24,
    "day_of_week": 7,
    "day_of_month": 32,
    "month_of_year": 13,
}


class DisplayRelatedField(BasePrimaryKeyRelatedField):
    """对象关联字段展示增强：补充 label（默认 str(instance)），
    供前端列表/详情/下拉直接可读，避免展示裸外键主键（周期任务页的
    crontab/interval 原先显示 1/2/3 这种数字主键，无法辨认）。"""

    def __init__(self, *args, label_builder=str, **kwargs):
        self.label_builder = label_builder
        super().__init__(*args, **kwargs)

    def to_representation(self, value):
        data = super().to_representation(value)
        if isinstance(data, dict):
            # 基类已把 label 兜底为 pk，这里必须覆盖而非 setdefault，否则 label_builder 永不生效
            data["label"] = self.label_builder(value)
        return data


def crontab_display(value: CrontabSchedule) -> str:
    """标准 cron 文本序（分 时 日 月 周），并附时区；替代含文档噪声的 __str__。"""
    tz = f" ({value.timezone})" if value.timezone else ""
    return f"{value.minute} {value.hour} {value.day_of_month} {value.month_of_year} {value.day_of_week}{tz}"


class CrontabScheduleSerializer(BaseModelSerializer):
    # CrontabSchedule.timezone 为 TimeZoneField，取值是 ZoneInfo 对象，无法直接 JSON 序列化，按字符串读写
    # 显式传 null 直接 400（调度时区必填）；字段缺省时仍按 CELERY_TIMEZONE 落默认值
    timezone = serializers.CharField(required=False, default=settings.CELERY_TIMEZONE, label=_("Timezone"))

    class Meta:
        model = CrontabSchedule
        fields = "__all__"
        table_fields = ["pk", "minute", "hour", "day_of_week", "day_of_month", "month_of_year", "timezone"]

    def validate(self, attrs):
        """五字段合法性校验，防止存入 beat 无法解析的 crontab。

        编辑（PUT/PATCH 部分字段）时 attrs 可能缺某字段，用 instance 兜底；
        创建时五字段全量校验。
        """
        instance = self.instance
        for field in ("minute", "hour", "day_of_week", "day_of_month", "month_of_year"):
            value = attrs.get(field)
            if value is None and instance:
                value = getattr(instance, field)
            if value in (None, ""):
                continue
            try:
                crontab_parser(_CRONTAB_STEPS[field]).parse(str(value))
            except ValueError as exc:
                raise serializers.ValidationError({field: _("Invalid crontab expression: {}").format(exc)}) from exc
        return attrs


class IntervalScheduleSerializer(BaseModelSerializer):
    class Meta:
        model = IntervalSchedule
        fields = "__all__"
        table_fields = ["pk", "every", "period"]

    def validate(self, attrs):
        """间隔唯一性校验（django_celery_beat>=2.9 已移除库级 unique_together）。

        完全相同的 (every, period) 会在周期任务表单的「执行间隔」下拉里出现
        多个无法分辨的同名项（如两条「每 15 分钟」），这里在业务侧补回该校验；
        编辑时排除自身，避免仅调整其他字段时被自身误拒。
        """
        every = attrs.get("every", getattr(self.instance, "every", None))
        period = attrs.get("period", getattr(self.instance, "period", None))
        if every is None or not period:
            return attrs
        queryset = IntervalSchedule.objects.filter(every=every, period=period)
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError(_("A schedule with the same interval already exists"))
        return attrs

    def create(self, validated_data):
        """(every, period) 按 get_or_create 语义落库，消除查重校验与写入之间的竞态窗口。

        validate 的查重负责用户可读报错，但校验通过到写入之间另一请求可能已
        落库同组合（库级自 2.9 起无唯一约束兜底）：命中即复用既有行、未命中
        才建，写入若撞上唯一约束则由 get_or_create 回查复用兜底；库级无约束时
        极端并发窗口仍可能双插，属应用层无法根除的残余风险。
        """
        lookup = {field: validated_data[field] for field in ("every", "period")}
        defaults = {key: value for key, value in validated_data.items() if key not in lookup}
        with transaction.atomic():
            instance, _created = IntervalSchedule.objects.get_or_create(defaults=defaults, **lookup)
        return instance


def _validate_json_string(raw, expect_type, field_label):
    """args/kwargs 以 JSON 字符串落库（django_celery_beat 约定），入库前校验可解析且类型正确。"""
    if raw in (None, ""):
        return raw
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise serializers.ValidationError(_("%(label)s must be a valid JSON string") % {"label": field_label}) from None
    if not isinstance(data, expect_type):
        raise serializers.ValidationError(
            _("%(label)s must be JSON %(type)s")
            % {
                "label": field_label,
                "type": "array" if expect_type is list else "object",
            }
        )
    return raw


def _task_routes_config() -> dict:
    """取当前生效的任务路由表（dict 形态）。

    `CELERY_TASK_ROUTES` 支持两种形态：静态 dict（用户在 config.yml 覆盖）与
    可调用路由（common/celery/routing.py：内置表 + 各应用 config.TASK_ROUTES 声明，
    celery 每次投递时求值）。需要枚举全部路由的消费方（队列/交换机下拉推导）
    统一走此处取静态合并视图。
    """
    routes = getattr(settings, "CELERY_TASK_ROUTES", None)
    if callable(routes):
        from common.celery.routing import get_all_task_routes

        return get_all_task_routes()
    return routes or {}


def _task_routing_options():
    """从 celery 配置推导可投递的队列（单一事实源，避免前端手填拼错）。

    取值 = 默认队列（CELERY_TASK_DEFAULT_QUEUE，缺省 celery）+ CELERY_TASK_ROUTES
    中显式路由到的队列；新增队列只需声明路由（settings 或应用 config.TASK_ROUTES），
    下拉选项自动跟随。
    """
    routes = _task_routes_config()
    options = []
    default_queue = getattr(settings, "CELERY_TASK_DEFAULT_QUEUE", None) or "celery"
    if default_queue:
        options.append(default_queue)
    for route in routes.values():
        # CELERY_TASK_ROUTES 值可为 dict（{'queue': ...}）或字符串（直接指定队列名）
        queue = route.get("queue") if isinstance(route, dict) else route
        if queue and queue not in options:
            options.append(queue)
    return options


def _queue_choices():
    # 留空表示 celery 默认路由（对应 beat 模型 queue=None）
    return [("", _("Default queue"))] + [(queue, queue) for queue in _task_routing_options()]


def _routing_key_choices():
    # 默认路由键即队列名，选项与队列一致
    return [("", _("Default routing key"))] + [(queue, queue) for queue in _task_routing_options()]


def _exchange_choices():
    # 项目 broker 为默认直接交换机（空名），未配置命名交换机时仅保留默认项
    routes = _task_routes_config()
    options = []
    for route in routes.values():
        exchange = route.get("exchange") if isinstance(route, dict) else None
        if exchange and exchange not in options:
            options.append(exchange)
    return [("", _("Default exchange"))] + [(exchange, exchange) for exchange in options]


def _validate_task_runnable(name) -> str:
    """task 字段白名单校验：仅白名单内的任务可被配置为周期任务。

    celery 注册表里的任务即系统全部 @shared_task（含删数据/改密等高危任务），
    不设白名单等于把任务执行权完全暴露给管理面；执行侧（run/batch-run）另有
    同口径拦截，只挡写入不挡执行会让存量任务绕过。
    """
    from task.utils.task_whitelist import is_task_runnable

    name = str(name or "").strip()
    if not name:
        raise serializers.ValidationError(_("Task name is required"))
    if not is_task_runnable(name):
        raise serializers.ValidationError(
            _('Task "{}" is not allowed for manual scheduling (not in the runnable whitelist)').format(name)
        )
    return name


def _validate_task_registered(name) -> str:
    """task 字段存在性校验：未注册的任务路径在保存时即拒绝，而非等到执行才报错。

    与白名单校验（_validate_task_runnable）并列同链，白名单先行；口径与执行
    侧（run/batch-run）一致——先查注册表，缺失时补一次全量注册（进程内只扫
    一次）再复查。经函数级导入视图层的注册入口，规避 serializers ↔ views 循环。
    """
    from server.celery import app
    from task.views.task_periodic import ensure_tasks_registered

    if name not in app.tasks:
        ensure_tasks_registered()
    if name not in app.tasks:
        raise serializers.ValidationError(_('Task "{}" is not registered').format(name))
    return name


class PeriodicTaskSerializer(BaseModelSerializer):
    # 调度关联默认只序列化 {pk}，前端显示为数字主键不可读；
    # 换用带 label 的关联字段：列表/详情/下拉直接显示
    # "0 4 * * * (Asia/Shanghai)" / "每 60 秒"（str 走 gettext 本地化）
    crontab = DisplayRelatedField(
        queryset=CrontabSchedule.objects.all(),
        label_builder=crontab_display,
        required=False,
        allow_null=True,
        label=_("Crontab"),
    )
    interval = DisplayRelatedField(
        queryset=IntervalSchedule.objects.all(),
        required=False,
        allow_null=True,
        label=_("Interval"),
    )
    # args/kwargs 为 JSON 字符串（TextField），显式声明以校验可解析且类型正确；
    # required=False + allow_blank=True 与模型 default '[]'/'{}' 兼容（缺省走模型默认）
    args = serializers.CharField(
        required=False,
        allow_blank=True,
        label=_("Positional Args"),
        validators=[lambda raw: _validate_json_string(raw, list, _("Positional Args"))],
    )
    kwargs = serializers.CharField(
        required=False,
        allow_blank=True,
        label=_("Keyword Args"),
        validators=[lambda raw: _validate_json_string(raw, dict, _("Keyword Args"))],
    )
    # 队列/路由键/交换机改为配置驱动下拉（choices 由 celery 配置推导），
    # 避免前端手填拼写错误；留空均表示默认路由
    queue = serializers.ChoiceField(
        choices=_queue_choices(),
        required=False,
        allow_null=True,
        label=_("Queue"),
    )
    routing_key = serializers.ChoiceField(
        choices=_routing_key_choices(),
        required=False,
        allow_null=True,
        label=_("Routing Key"),
    )
    exchange = serializers.ChoiceField(
        choices=_exchange_choices(),
        required=False,
        allow_null=True,
        label=_("Exchange"),
    )
    # 任务名白名单 + 注册存在性：仅可手动执行清单内的任务、且当前进程注册表
    # 里真实存在的任务可被配置（路径写错在保存时报 400，而非执行时才失败）
    task = serializers.CharField(label=_("Task"), validators=[_validate_task_runnable, _validate_task_registered])

    class Meta:
        model = PeriodicTask
        fields = "__all__"
        table_fields = [
            # 顺序即列表列序：enabled（启停开关，行内可交互）紧跟在任务名之后——
            # 放右侧时会被固定操作列覆盖（可点区域落在覆盖区内，开关点不动）
            "pk",
            "name",
            "enabled",
            "task",
            "crontab",
            "interval",
            "one_off",
            "last_run_at",
            "total_run_count",
            "date_changed",
            "description",
        ]


class TaskExecutionSerializer(BaseModelSerializer):
    # 执行历史为只读资源，关联字段显式声明为带 label 的展示字段（显式声明
    # 不受 Meta.read_only_fields 作用，必须自带 read_only=True；DRF 禁止
    # read_only 字段携带 queryset），前端列表直接显示任务名 / 昵称(用户名)，
    # 而非裸数字主键。PeriodicTask.__str__ 附带 schedule 噪声，这里仅取任务名
    periodic_task = DisplayRelatedField(
        read_only=True,
        allow_null=True,
        label=_("Periodic Task"),
        label_builder=lambda value: value.name,
    )
    creator = DisplayRelatedField(
        read_only=True,
        allow_null=True,
        label=_("Creator"),
    )
    # 执行状态字典化：管理员可在数据字典 task_status 维护文案/颜色（默认项随种子下发），
    # merge 保证字典项被删/未配齐时枚举标签兜底
    status = DictChoiceField(
        dict_code="task_status",
        fallback_choices=TaskExecution.Status.choices,
        merge_fallback=True,
        read_only=True,
    )
    time_cost = serializers.SerializerMethodField(label=_("Time Cost"))
    # 产物信息（列表动作按 pk 子查询带出；定时/即时任务为空值）：
    # 导出/导入任务与执行记录共用主键，同一行即可表达类型、业务名、进度与产物文件
    product_type = serializers.SerializerMethodField(label=_("Record type"))
    product_name = serializers.SerializerMethodField(label=_("Record name"))
    product_progress = serializers.SerializerMethodField(label=_("Progress"))
    product_stage = serializers.SerializerMethodField(label=_("Progress stage"))
    product_error = serializers.SerializerMethodField(label=_("Error message"))
    product_has_file = serializers.SerializerMethodField(label=_("Output file"))
    can_cancel = serializers.SerializerMethodField(label=_("Can cancel"))
    can_rerun = serializers.SerializerMethodField(label=_("Can rerun"))

    class Meta:
        model = TaskExecution
        fields = [
            "pk",
            "name",
            "product_type",
            "product_name",
            "product_progress",
            "product_stage",
            "product_error",
            "product_has_file",
            "can_cancel",
            "can_rerun",
            "periodic_task",
            "args",
            "kwargs",
            "status",
            "creator",
            "time_cost",
            "created_time",
            "date_start",
            "date_finished",
        ]
        table_fields = [
            "pk",
            "product_type",
            "name",
            "periodic_task",
            "status",
            "product_progress",
            "time_cost",
            "creator",
            "created_time",
            "product_error",
            "date_start",
            "date_finished",
        ]
        read_only_fields = fields

    def _product_data(self, obj) -> dict:
        """列表注解合并下发的产物信息（类型/业务名/进度/阶段/错误）。

        仅列表动作带 product_data 注解；详情等其它动作按空表降级，取值语义
        与拆分前的逐字段注解一致。
        """
        data = getattr(obj, "product_data", None)
        return data if isinstance(data, dict) else {}

    def get_product_type(self, obj) -> str:
        """产物类型（export/import；定时与即时任务为空）。"""
        return str(self._product_data(obj).get("type") or "")

    def get_product_name(self, obj) -> str:
        """产物记录的业务名（如「用户导出-20260924120000」），非产物任务为空。"""
        return str(self._product_data(obj).get("name") or "")

    def get_product_progress(self, obj) -> int:
        return int(self._product_data(obj).get("progress") or 0)

    def get_product_stage(self, obj) -> str:
        return str(self._product_data(obj).get("stage") or "")

    def get_product_error(self, obj) -> str:
        return str(self._product_data(obj).get("error") or "")[:500]

    def get_product_has_file(self, obj) -> bool:
        return bool(getattr(obj, "product_has_file", False))

    def get_can_cancel(self, obj) -> bool:
        return str(obj.status) in ACTIVE_STATUSES

    def get_can_rerun(self, obj) -> bool:
        """仅产物类（导出/导入）任务在终态后可重跑（白名单口径与任务中心一致）。"""
        return bool(self.get_product_type(obj)) and str(obj.status) not in ACTIVE_STATUSES

    def get_time_cost(self, obj):
        cost = obj.time_cost
        return round(cost, 3) if cost is not None else None
