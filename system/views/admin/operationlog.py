#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : operationlog
# author : ly_13
# date : 6/27/2023

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet, PkMultipleFilter
from common.core.modelset import ListDeleteModelSet, OnlyExportDataAction
from system.models import OperationLog
from system.serializers.log import OperationLogSerializer


class OperationLogFilter(BaseFilterSet):
    ipaddress = filters.CharFilter(field_name='ipaddress', lookup_expr='icontains')
    system = filters.CharFilter(field_name='system', lookup_expr='icontains')
    path = filters.CharFilter(field_name='path', lookup_expr='icontains')
    error_status = filters.BooleanFilter(method='get_error_status', label=_('Error status'))

    def get_error_status(self, queryset, name, value):
        if value is True:
            return queryset.exclude(status_code=1000)
        return queryset.filter(status_code=1000)

    # 自定义的搜索模板，需要前端同时添加 userinfo 类型
    creator_id = PkMultipleFilter(input_type='api-search-user')

    class Meta:
        model = OperationLog
        fields = ['request_uuid', 'module', 'ipaddress', 'system', 'creator_id', 'status_code', 'path', 'created_time',
                  'error_status']


class OperationLogViewSet(ListDeleteModelSet, OnlyExportDataAction):
    """操作日志"""
    queryset = OperationLog.objects.all()
    serializer_class = OperationLogSerializer

    ordering_fields = ['created_time', 'updated_time', 'exec_time']
    filterset_class = OperationLogFilter
