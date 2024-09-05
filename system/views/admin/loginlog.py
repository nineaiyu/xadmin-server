#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : loginlog
# author : ly_13
# date : 1/3/2024


from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet, PkMultipleFilter
from common.core.modelset import ListDeleteModelSet, OnlyExportDataAction
from system.models import UserLoginLog
from system.serializers.log import UserLoginLogSerializer


class LoginLogFilter(BaseFilterSet):
    ipaddress = filters.CharFilter(field_name='ipaddress', lookup_expr='icontains')
    city = filters.CharFilter(field_name='city', lookup_expr='icontains')
    system = filters.CharFilter(field_name='system', lookup_expr='icontains')
    browser = filters.CharFilter(field_name='browser', lookup_expr='icontains')
    agent = filters.CharFilter(field_name='agent', lookup_expr='icontains')
    creator_id = PkMultipleFilter(input_type='api-search-user')

    class Meta:
        model = UserLoginLog
        fields = ['login_type', 'ipaddress', 'city', 'system', 'creator_id', 'browser', 'agent', 'created_time']


class LoginLogView(ListDeleteModelSet, OnlyExportDataAction):
    """用户登录日志"""
    queryset = UserLoginLog.objects.all()
    serializer_class = UserLoginLogSerializer

    ordering_fields = ['created_time']
    filterset_class = LoginLogFilter
