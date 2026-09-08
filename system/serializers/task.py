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
from django.utils.translation import gettext_lazy as _
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask
from rest_framework import serializers

from common.core.serializers import BasePrimaryKeyRelatedField, BaseModelSerializer
from system.models.task import TaskExecution

# celery crontab_parser 各字段的取值跨度（min-max 由 parser 按 steps 推导）
_CRONTAB_STEPS = {
    "minute": 60, "hour": 24, "day_of_week": 7,
    "day_of_month": 32, "month_of_year": 13,
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
    return (
        f"{value.minute} {value.hour} {value.day_of_month} "
        f"{value.month_of_year} {value.day_of_week}{tz}"
    )


class CrontabScheduleSerializer(BaseModelSerializer):
    # CrontabSchedule.timezone 为 TimeZoneField，取值是 ZoneInfo 对象，无法直接 JSON 序列化，按字符串读写
    timezone = serializers.CharField(required=False, allow_null=True,
                                     default=settings.CELERY_TIMEZONE, label=_("Timezone"))

    class Meta:
        model = CrontabSchedule
        fields = "__all__"
        table_fields = ['pk', 'minute', 'hour', 'day_of_week', 'day_of_month', 'month_of_year', 'timezone']

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
                raise serializers.ValidationError(
                    {field: _('Invalid crontab expression: {}').format(exc)}
                )
        return attrs


class IntervalScheduleSerializer(BaseModelSerializer):
    class Meta:
        model = IntervalSchedule
        fields = "__all__"
        table_fields = ['pk', 'every', 'period']


def _validate_json_string(raw, expect_type, field_label):
    """args/kwargs 以 JSON 字符串落库（django_celery_beat 约定），入库前校验可解析且类型正确。"""
    if raw in (None, ""):
        return raw
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise serializers.ValidationError(
            _('%(label)s must be a valid JSON string') % {'label': field_label}
        )
    if not isinstance(data, expect_type):
        raise serializers.ValidationError(
            _('%(label)s must be JSON %(type)s') % {
                'label': field_label,
                'type': 'array' if expect_type is list else 'object',
            }
        )
    return raw


def _task_routing_options():
    """从 celery 配置推导可投递的队列（单一事实源，避免前端手填拼错）。

    取值 = 默认队列（CELERY_TASK_DEFAULT_QUEUE，缺省 celery）+ CELERY_TASK_ROUTES
    中显式路由到的队列；新增队列只需在 settings 配置，下拉选项自动跟随。
    """
    routes = getattr(settings, 'CELERY_TASK_ROUTES', None) or {}
    options = []
    default_queue = getattr(settings, 'CELERY_TASK_DEFAULT_QUEUE', None) or 'celery'
    if default_queue:
        options.append(default_queue)
    for route in routes.values():
        # CELERY_TASK_ROUTES 值可为 dict（{'queue': ...}）或字符串（直接指定队列名）
        queue = route.get('queue') if isinstance(route, dict) else route
        if queue and queue not in options:
            options.append(queue)
    return options


def _queue_choices():
    # 留空表示 celery 默认路由（对应 beat 模型 queue=None）
    return [('', _('Default queue'))] + [(queue, queue) for queue in _task_routing_options()]


def _routing_key_choices():
    # 默认路由键即队列名，选项与队列一致
    return [('', _('Default routing key'))] + [(queue, queue) for queue in _task_routing_options()]


def _exchange_choices():
    # 项目 broker 为默认直接交换机（空名），未配置命名交换机时仅保留默认项
    routes = getattr(settings, 'CELERY_TASK_ROUTES', None) or {}
    options = []
    for route in routes.values():
        exchange = route.get('exchange') if isinstance(route, dict) else None
        if exchange and exchange not in options:
            options.append(exchange)
    return [('', _('Default exchange'))] + [(exchange, exchange) for exchange in options]


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
        required=False, allow_blank=True, label=_("Positional Args"),
        validators=[lambda raw: _validate_json_string(raw, list, _("Positional Args"))],
    )
    kwargs = serializers.CharField(
        required=False, allow_blank=True, label=_("Keyword Args"),
        validators=[lambda raw: _validate_json_string(raw, dict, _("Keyword Args"))],
    )
    # 队列/路由键/交换机改为配置驱动下拉（choices 由 celery 配置推导），
    # 避免前端手填拼写错误；留空均表示默认路由
    queue = serializers.ChoiceField(
        choices=_queue_choices(), required=False, allow_null=True, label=_("Queue"),
    )
    routing_key = serializers.ChoiceField(
        choices=_routing_key_choices(), required=False, allow_null=True,
        label=_("Routing Key"),
    )
    exchange = serializers.ChoiceField(
        choices=_exchange_choices(), required=False, allow_null=True, label=_("Exchange"),
    )

    class Meta:
        model = PeriodicTask
        fields = "__all__"
        table_fields = ['pk', 'name', 'task', 'crontab', 'interval', 'enabled', 'one_off',
                        'last_run_at', 'total_run_count', 'date_changed', 'description']


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
    time_cost = serializers.SerializerMethodField(label=_("Time Cost"))

    class Meta:
        model = TaskExecution
        fields = ['pk', 'name', 'periodic_task', 'args', 'kwargs', 'status',
                  'creator', 'time_cost', 'created_time', 'date_start', 'date_finished']
        table_fields = ['pk', 'name', 'periodic_task', 'status', 'creator',
                        'time_cost', 'created_time', 'date_start', 'date_finished']
        read_only_fields = fields

    def get_time_cost(self, obj):
        cost = obj.time_cost
        return round(cost, 3) if cost is not None else None