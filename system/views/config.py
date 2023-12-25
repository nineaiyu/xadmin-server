#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : config
# author : ly_13
# date : 6/16/2023
import logging

from django_filters import rest_framework as filters

from common.core.modelset import BaseModelSet
from common.core.pagination import MenuPageNumber
from system.models import SystemConfig
from system.utils.modelset import ChangeRolePermissionAction
from system.utils.serializer import SystemConfigSerializer

logger = logging.getLogger(__name__)


class SystemConfigFilter(filters.FilterSet):
    key = filters.CharFilter(field_name='key', lookup_expr='icontains')
    description = filters.CharFilter(field_name='description', lookup_expr='icontains')
    value = filters.CharFilter(field_name='value', lookup_expr='icontains')
    pk = filters.NumberFilter(field_name='id')

    class Meta:
        model = SystemConfig
        fields = ['pk', 'is_active', 'key']


class SystemConfigView(BaseModelSet, ChangeRolePermissionAction):
    queryset = SystemConfig.objects.all()
    serializer_class = SystemConfigSerializer
    pagination_class = MenuPageNumber

    ordering_fields = ['created_time']
    filterset_class = SystemConfigFilter
