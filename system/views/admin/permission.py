#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : permission
# author : ly_13
# date : 6/16/2023

from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, ImportExportDataAction
from common.utils import get_logger
from system.models import DataPermission
from system.serializers.permission import DataPermissionSerializer

logger = get_logger(__name__)


class DataPermissionFilter(BaseFilterSet):
    pk = filters.UUIDFilter(field_name='id')
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')

    class Meta:
        model = DataPermission
        fields = ['pk', 'name', 'mode_type', 'is_active', 'description']


class DataPermissionViewSet(BaseModelSet, ImportExportDataAction):
    """数据权限"""
    queryset = DataPermission.objects.all()
    serializer_class = DataPermissionSerializer
    ordering_fields = ['created_time']
    filterset_class = DataPermissionFilter
