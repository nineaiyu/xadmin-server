#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : dashboard
# author : ly_13
# date : 3/13/2024
import datetime

from django.db.models import Count
from django.db.models.functions import TruncDay
from django.utils import timezone
from drf_spectacular.plumbing import build_object_type, build_basic_type, build_array_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet

from common.base.magic import cache_response
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models import UserLoginLog, OperationLog, UserInfo
from system.serializers.log import LoginLogSerializer


def trend_info(queryset, limit_day=30, total_count=True):
    """按天聚合趋势数据。

    :param total_count: True 返回整表总数；False 只统计趋势窗口内的数量。
        日志类大表（OperationLog）全表 COUNT 代价随数据增长线性上升，
        而前端该卡片并不使用 count 字段，故改为窗口内计数。
    """
    # 必须使用本地时间：TruncDay 按 TIME_ZONE 分桶，而 strftime 是朴素格式化，
    # 直接用 timezone.now()（UTC）会导致日期标签与聚合结果整体错位一天
    today = timezone.localtime(timezone.now())
    limit_days = today - datetime.timedelta(days=limit_day, hours=today.hour, minutes=today.minute,
                                            seconds=today.second, microseconds=today.microsecond)
    window_queryset = queryset.filter(created_time__gte=limit_days)
    data_count = window_queryset.annotate(
        created_time_day=TruncDay('created_time')).values(
        'created_time_day').annotate(count=Count('pk')).order_by('-created_time_day')
    dict_count = {d.get('created_time_day').strftime('%m-%d'): d.get('count') for d in data_count}
    results = []
    for i in range(limit_day, -1, -1):
        date = (today - datetime.timedelta(days=i)).strftime('%m-%d')
        results.append({'day': date, 'count': dict_count[date] if date in dict_count else 0})
    if len(results) > 1:
        x, y = results[-1].get('count'), results[-2].get('count')
        # 环比增长率：(今日 - 昨日) / 昨日；昨日为 0 时，今日有数据视为 100%，否则 0
        percent = round(100 * (x - y) / y, 2) if y else (100.0 if x else 0.0)
    else:
        percent = 0

    return results, percent, queryset.count() if total_count else window_queryset.count()


def get_schema_response(has_count=True):
    ext = {}
    if has_count:
        ext = {
            'percent': build_basic_type(OpenApiTypes.NUMBER),
            'count': build_basic_type(OpenApiTypes.NUMBER),
        }
    return get_default_response_schema(
        {
            'results': build_array_type(
                build_object_type(
                    properties={
                        'day': build_basic_type(OpenApiTypes.STR),
                        'count': build_basic_type(OpenApiTypes.NUMBER),
                    }
                )
            ),
            **ext
        }
    )


class DashboardViewSet(GenericViewSet):
    """面板统计信息"""
    queryset = UserLoginLog.objects.all()
    serializer_class = LoginLogSerializer
    ordering_fields = ['created_time']
    # PERF-02：面板数据对实时性不敏感，短缓存避免多端同时刷新时重复全表聚合
    dashboard_cache_timeout = 60

    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f'{view_instance.__class__.__name__}_{view_method.__name__}'
        return f"{func_name}_{request.user.pk}"

    @extend_schema(responses=get_schema_response())
    @action(methods=['GET'], detail=False, url_path='user-login-total')
    @cache_response(timeout=60, key_func='get_cache_key')
    def user_login_total(self, request, *args, **kwargs):
        """{cls}-用户登录"""
        results, percent, count = trend_info(self.filter_queryset(self.get_queryset()), 7)
        return ApiResponse(results=results, percent=percent, count=count)

    @extend_schema(responses=get_schema_response())
    @action(methods=['GET'], detail=False, queryset=UserInfo.objects.all(), url_path='user-total')
    @cache_response(timeout=60, key_func='get_cache_key')
    def user_total(self, request, *args, **kwargs):
        """{cls}-用户数量"""
        results, percent, count = trend_info(self.filter_queryset(self.get_queryset()), 7)
        return ApiResponse(results=results, percent=percent, count=count)

    @extend_schema(responses=get_schema_response(False))
    @action(methods=['GET'], detail=False, queryset=UserInfo.objects.all(), url_path='user-registered-trend')
    @cache_response(timeout=60, key_func='get_cache_key')
    def user_registered_trend(self, request, *args, **kwargs):
        """{cls}-注册报表"""
        return ApiResponse(data=trend_info(self.filter_queryset(self.get_queryset()))[0])

    @extend_schema(responses=get_schema_response(False))
    @action(methods=['GET'], detail=False, url_path='user-login-trend')
    @cache_response(timeout=60, key_func='get_cache_key')
    def user_login_trend(self, request, *args, **kwargs):
        """{cls}-登录报表"""
        return ApiResponse(data=trend_info(self.filter_queryset(self.get_queryset()))[0])

    @extend_schema(responses=get_schema_response())
    @action(methods=['GET'], detail=False, queryset=OperationLog.objects.all(), url_path='today-operate-total')
    @cache_response(timeout=60, key_func='get_cache_key')
    def today_operate_total(self, request, *args, **kwargs):
        """{cls}-最近操作日志"""
        # 前端该卡片只使用 results/percent，不使用 count，故按趋势窗口计数，避免全表 COUNT
        results, percent, count = trend_info(self.filter_queryset(self.get_queryset()), 7, total_count=False)
        return ApiResponse(results=results, percent=percent, count=count)

    @extend_schema(
        responses=get_default_response_schema(
            {
                'data': build_array_type(build_array_type(build_basic_type(OpenApiTypes.NUMBER)))
            }
        )
    )
    @action(methods=['GET'], detail=False, queryset=UserInfo.objects.all(), url_path='user-active')
    @cache_response(timeout=60, key_func='get_cache_key')
    def user_active(self, request, *args, **kwargs):
        """{cls}-活跃用户"""
        # 与 trend_info 保持一致：按本地时间切分自然日
        today = timezone.localtime(timezone.now())
        active_date_list = [1, 3, 7, 30]
        results = []
        queryset = self.filter_queryset(self.get_queryset())
        for date in active_date_list:
            x_day = today - datetime.timedelta(days=date - 1, hours=today.hour, minutes=today.minute,
                                               seconds=today.second, microseconds=today.microsecond)
            x_day_register_user = queryset.filter(date_joined__gte=x_day).count()
            # values('last_login').annotate(...).count() 统计的是不同 last_login 值的个数，
            # 同一秒登录的多个用户会被合并成 1，导致活跃用户数被系统性低估
            x_day_active_user = queryset.filter(last_login__gte=x_day).count()
            results.append([date, x_day_register_user, x_day_active_user])
        return ApiResponse(data=results)
