#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : loginlog
# author : ly_13
# date : 1/3/2024


from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet
from common.core.modelset import ListDeleteModelSet
from system.models import UserLoginLog
from system.utils.serializer import UserLoginLogSerializer


class UserLoginLogFilter(BaseFilterSet):
    ipaddress = filters.CharFilter(field_name='ipaddress', lookup_expr='icontains')
    system = filters.CharFilter(field_name='system', lookup_expr='icontains')
    browser = filters.CharFilter(field_name='browser', lookup_expr='icontains')
    agent = filters.CharFilter(field_name='agent', lookup_expr='icontains')
    creator_id = filters.NumberFilter(field_name='creator__id')

    class Meta:
        model = UserLoginLog
        fields = ['creator_id', 'login_type', 'ipaddress', 'system', 'browser', 'agent', 'created_time']


class UserLoginLogView(ListDeleteModelSet):
    """用户登录日志"""
    queryset = UserLoginLog.objects.all()
    serializer_class = UserLoginLogSerializer

    ordering_fields = ['created_time']
    filterset_class = UserLoginLogFilter
