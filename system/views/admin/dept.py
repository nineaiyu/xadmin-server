#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : dept
# author : ly_13
# date : 6/16/2023
from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, ImportExportDataAction
from common.core.pagination import DynamicPageNumber
from common.utils import get_logger
from system.models import DeptInfo
from system.serializers.department import DeptSerializer
from system.utils.modelset import ChangeRolePermissionAction

logger = get_logger(__name__)


class DeptFilter(BaseFilterSet):
    pk = filters.UUIDFilter(field_name='id')
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')

    class Meta:
        model = DeptInfo
        fields = ['pk', 'is_active', 'code', 'mode_type', 'auto_bind', 'name', 'description']


class DeptViewSet(BaseModelSet, ChangeRolePermissionAction, ImportExportDataAction):
    """部门"""
    queryset = DeptInfo.objects.all()
    serializer_class = DeptSerializer
    pagination_class = DynamicPageNumber(1000)
    ordering_fields = ['created_time', 'rank']
    filterset_class = DeptFilter
