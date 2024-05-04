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
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet

from common.core.response import ApiResponse
from system.models import UserLoginLog, UserInfo, OperationLog
from system.utils.serializer import UserLoginLogSerializer


def trend_info(queryset, limit_day=30):
    today = timezone.now()
    limit_days = today - datetime.timedelta(days=limit_day, hours=today.hour, minutes=today.minute,
                                            seconds=today.second, microseconds=today.microsecond)
    data_count = queryset.filter(created_time__gte=limit_days).annotate(
        date=TruncDay('created_time')).values(
        'date').annotate(count=Count('pk')).order_by('-date')
    dict_count = {d.get('date').strftime('%m-%d'): d.get('count') for d in data_count}
    results = []
    for i in range(limit_day, -1, -1):
        date = (today - datetime.timedelta(days=i)).strftime('%m-%d')
        results.append({'day': date, 'count': dict_count[date] if date in dict_count else 0})
    if len(results) > 1:
        y = results[-2].get('count')
        percent = round(100 * (results[-1].get('count') - y) / 1 if y == 0 else y)
    else:
        percent = 0

    return results, percent, queryset.count()


class DashboardView(GenericViewSet):
    """面板统计信息"""
    queryset = UserLoginLog.objects.all()
    serializer_class = UserLoginLogSerializer
    ordering_fields = ['created_time']

    @action(methods=['GET'], detail=False, url_path='user-login-total')
    def user_login_total(self, request, *args, **kwargs):
        results, percent, count = trend_info(self.filter_queryset(self.get_queryset()), 7)
        return ApiResponse(results=results, percent=percent, count=count)

    @action(methods=['GET'], detail=False, queryset=UserInfo.objects.all(), url_path='user-total')
    def user_total(self, request, *args, **kwargs):
        results, percent, count = trend_info(self.filter_queryset(self.get_queryset()), 7)
        return ApiResponse(results=results, percent=percent, count=count)

    @action(methods=['GET'], detail=False, queryset=UserInfo.objects.all(), url_path='user-registered-trend')
    def user_registered_trend(self, request, *args, **kwargs):
        return ApiResponse(results=trend_info(self.filter_queryset(self.get_queryset()))[0])

    @action(methods=['GET'], detail=False, url_path='user-login-trend')
    def user_login_trend(self, request, *args, **kwargs):
        return ApiResponse(results=trend_info(self.filter_queryset(self.get_queryset()))[0])

    @action(methods=['GET'], detail=False, queryset=OperationLog.objects.all(), url_path='today-operate-total')
    def today_operate_total(self, request, *args, **kwargs):
        results, percent, count = trend_info(self.filter_queryset(self.get_queryset()), 7)
        return ApiResponse(results=results, percent=percent, count=count)

    @action(methods=['GET'], detail=False, queryset=UserInfo.objects.all(), url_path='user-active')
    def user_active(self, request, *args, **kwargs):
        today = timezone.now()
        active_date_list = [1, 3, 7, 30]
        results = []
        queryset = self.filter_queryset(self.get_queryset())
        for date in active_date_list:
            x_day = today - datetime.timedelta(days=date - 1, hours=today.hour, minutes=today.minute,
                                               seconds=today.second, microseconds=today.microsecond)
            x_day_register_user = queryset.filter(date_joined__gte=x_day).count()
            x_day_active_user = queryset.filter(last_login__gte=x_day).values('last_login').annotate(
                count=Count('pk', distinct=True)).count()
            results.append([date, x_day_register_user, x_day_active_user])
        return ApiResponse(results=results)
