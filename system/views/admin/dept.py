#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : dept
# author : ly_13
# date : 6/16/2023
import logging

from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet
from common.core.pagination import DynamicPageNumber
from system.models import DeptInfo
from system.utils.modelset import ChangeRolePermissionAction
from system.utils.serializer import DeptSerializer

logger = logging.getLogger(__name__)


class DeptFilter(BaseFilterSet):
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')

    class Meta:
        model = DeptInfo
        fields = ['pk', 'is_active', 'code', 'mode_type', 'auto_bind', 'name', 'description']


class DeptView(BaseModelSet, ChangeRolePermissionAction):
    """
    部门信息
    """
    queryset = DeptInfo.objects.all()
    serializer_class = DeptSerializer
    pagination_class = DynamicPageNumber(1000)
    ordering_fields = ['created_time', 'rank']
    filterset_class = DeptFilter
