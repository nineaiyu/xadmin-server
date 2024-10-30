#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : role
# author : ly_13
# date : 7/22/2024

from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet
from common.core.modelset import OnlyListModelSet
from common.utils import get_logger
from system.models import UserRole
from system.serializers.role import RoleSerializer

logger = get_logger(__name__)


class SearchRoleFilter(BaseFilterSet):
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')
    code = filters.CharFilter(field_name='code', lookup_expr='icontains')

    class Meta:
        model = UserRole
        fields = ['name', 'code', 'is_active', 'description']


class SearchRoleSerializer(RoleSerializer):
    class Meta:
        model = UserRole
        fields = ['pk', 'name', 'code', 'is_active', 'description', 'updated_time']
        read_only_fields = [x.name for x in UserRole._meta.fields]


class SearchRoleViewSet(OnlyListModelSet):
    """角色搜索"""
    queryset = UserRole.objects.all()
    serializer_class = SearchRoleSerializer
    ordering_fields = ['updated_time', 'name', 'created_time']
    filterset_class = SearchRoleFilter
