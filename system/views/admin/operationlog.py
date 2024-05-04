#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : operationlog
# author : ly_13
# date : 6/27/2023

from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet
from common.core.modelset import ListDeleteModelSet
from system.models import OperationLog
from system.utils.serializer import OperationLogSerializer


class OperationLogFilter(BaseFilterSet):
    ipaddress = filters.CharFilter(field_name='ipaddress', lookup_expr='icontains')
    system = filters.CharFilter(field_name='system', lookup_expr='icontains')
    browser = filters.CharFilter(field_name='browser', lookup_expr='icontains')
    path = filters.CharFilter(field_name='path', lookup_expr='icontains')
    creator_id = filters.NumberFilter(field_name='creator__id')

    class Meta:
        model = OperationLog
        fields = ['module', 'creator_id', 'ipaddress', 'system', 'browser', 'path', 'created_time']


class OperationLogView(ListDeleteModelSet):
    """用户操作日志"""
    queryset = OperationLog.objects.all()
    serializer_class = OperationLogSerializer

    ordering_fields = ['created_time', 'updated_time']
    filterset_class = OperationLogFilter
