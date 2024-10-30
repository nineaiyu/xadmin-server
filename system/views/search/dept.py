#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : dept
# author : ly_13
# date : 7/22/2024

from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet
from common.core.modelset import OnlyListModelSet
from common.core.pagination import DynamicPageNumber
from common.utils import get_logger
from system.models import DeptInfo
from system.serializers.department import DeptSerializer

logger = get_logger(__name__)


class SearchDeptFilter(BaseFilterSet):
    pk = filters.UUIDFilter(field_name='id')
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')

    class Meta:
        model = DeptInfo
        fields = ['name', 'is_active', 'code', 'description']


class SearchDeptSerializer(DeptSerializer):
    class Meta:
        model = DeptInfo
        fields = ['name', 'pk', 'code', 'parent', 'is_active', 'user_count', 'auto_bind', 'description', 'created_time']
        table_fields = ['name', 'code', 'is_active', 'user_count', 'auto_bind', 'description', 'created_time', 'pk']
        read_only_fields = [x.name for x in DeptInfo._meta.fields]


class SearchDeptViewSet(OnlyListModelSet):
    """部门搜索"""
    queryset = DeptInfo.objects.all()
    serializer_class = SearchDeptSerializer
    pagination_class = DynamicPageNumber(1000)
    ordering_fields = ['created_time', 'rank']
    filterset_class = SearchDeptFilter
