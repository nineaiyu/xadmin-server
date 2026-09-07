#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""定时任务管理（django_celery_beat 业务侧 CRUD）。

提供周期任务 / crontab 表达式 / 固定间隔三类资源的管理接口，
替代此前仅能经 Django Admin 兜底管理的方式。启停变更由 beat 的
DatabaseScheduler 在 --max-interval（启动参数默认 60s）内感知生效。

数据权限沿用全局默认（未配置 DataPermission 规则的非超管不可见，默认拒绝），
按钮/菜单权限经菜单管理按 list:create:retrieve:partialUpdate:destroy:enable 配置。
"""
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.decorators import action

from common.core.response import ApiResponse
from common.core.serializers import (
    BasePrimaryKeyRelatedField,
    BaseModelSerializer,
)
from common.core.modelset import BaseModelSet
from common.swagger.utils import get_default_response_schema


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
            data.setdefault("label", self.label_builder(value))
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


class IntervalScheduleSerializer(BaseModelSerializer):
    class Meta:
        model = IntervalSchedule
        fields = "__all__"
        table_fields = ['pk', 'every', 'period']


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

    class Meta:
        model = PeriodicTask
        fields = "__all__"
        table_fields = ['pk', 'name', 'task', 'crontab', 'interval', 'enabled', 'one_off',
                        'last_run_at', 'total_run_count', 'date_changed', 'description']


class PeriodicTaskFilter(filters.FilterSet):
    """PeriodicTask 无 creator/dept 等审计字段，不继承 BaseFilterSet（其声明的过滤器引用不存在的字段）。"""
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')
    task = filters.CharFilter(field_name='task', lookup_expr='icontains')

    class Meta:
        model = PeriodicTask
        fields = ['enabled', 'one_off', 'queue']


class CrontabScheduleFilter(filters.FilterSet):
    class Meta:
        model = CrontabSchedule
        fields = ['minute', 'hour', 'day_of_week', 'month_of_year']


class IntervalScheduleFilter(filters.FilterSet):
    class Meta:
        model = IntervalSchedule
        fields = ['every', 'period']


class CrontabScheduleViewSet(BaseModelSet):
    """crontab 表达式管理"""
    queryset = CrontabSchedule.objects.all().order_by('minute', 'hour', 'day_of_week', 'month_of_year')
    serializer_class = CrontabScheduleSerializer
    filterset_class = CrontabScheduleFilter
    ordering = ['id']
    ordering_fields = ['id']


class IntervalScheduleViewSet(BaseModelSet):
    """固定间隔调度管理"""
    queryset = IntervalSchedule.objects.all().order_by('every', 'period')
    serializer_class = IntervalScheduleSerializer
    filterset_class = IntervalScheduleFilter
    ordering = ['id']
    ordering_fields = ['id']


class PeriodicTaskViewSet(BaseModelSet):
    """周期任务管理"""
    queryset = PeriodicTask.objects.all().order_by('name')
    serializer_class = PeriodicTaskSerializer
    filterset_class = PeriodicTaskFilter
    ordering = ['name']
    ordering_fields = ['name', 'enabled', 'date_changed']

    @extend_schema(
        request=build_object_type(properties={'enabled': build_basic_type(OpenApiTypes.BOOL)}),
        responses=get_default_response_schema(),
    )
    @action(methods=['patch'], detail=True)
    def enable(self, request, *args, **kwargs):
        """启用或停用{cls}任务"""
        instance = self.get_object()
        enabled = request.data.get('enabled')
        instance.enabled = (not instance.enabled) if enabled is None else bool(enabled)
        instance.save()
        return ApiResponse(data={'pk': instance.pk, 'enabled': instance.enabled})
