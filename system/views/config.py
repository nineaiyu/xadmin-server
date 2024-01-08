#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : config
# author : ly_13
# date : 6/16/2023
import logging

from django_filters import rest_framework as filters

from common.core.modelset import BaseModelSet
from system.models import SystemConfig, UserPersonalConfig
from system.utils.modelset import InvalidConfigCacheAction
from system.utils.serializer import SystemConfigSerializer, UserPersonalConfigSerializer

logger = logging.getLogger(__name__)


class SystemConfigFilter(filters.FilterSet):
    key = filters.CharFilter(field_name='key', lookup_expr='icontains')
    value = filters.CharFilter(field_name='value', lookup_expr='icontains')
    description = filters.CharFilter(field_name='description', lookup_expr='icontains')
    pk = filters.NumberFilter(field_name='id')

    class Meta:
        model = SystemConfig
        fields = ['pk', 'is_active', 'key']


class SystemConfigView(BaseModelSet, InvalidConfigCacheAction):
    queryset = SystemConfig.objects.all()
    serializer_class = SystemConfigSerializer
    ordering_fields = ['created_time']
    filterset_class = SystemConfigFilter


class UserPersonalConfigFilter(SystemConfigFilter):
    username = filters.CharFilter(field_name='owner__username')
    owner_id = filters.NumberFilter(field_name='owner__pk')

    class Meta:
        model = UserPersonalConfig
        fields = ['pk', 'is_active', 'key']


class UserPersonalConfigView(BaseModelSet, InvalidConfigCacheAction):
    queryset = UserPersonalConfig.objects.all()
    serializer_class = UserPersonalConfigSerializer
    ordering_fields = ['created_time']
    filterset_class = UserPersonalConfigFilter
