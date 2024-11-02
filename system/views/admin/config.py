#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : config
# author : ly_13
# date : 6/16/2023

from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema

from common.core.filter import BaseFilterSet, PkMultipleFilter
from common.core.modelset import BaseModelSet, ImportExportDataAction
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from system.models import SystemConfig, UserPersonalConfig
from system.serializers.config import SystemConfigSerializer, UserPersonalConfigSerializer, \
    UserPersonalConfigExportImportSerializer
from system.utils.modelset import InvalidConfigCacheAction

logger = get_logger(__name__)


class SystemConfigFilter(BaseFilterSet):
    pk = filters.UUIDFilter(field_name='id')
    key = filters.CharFilter(field_name='key', lookup_expr='icontains')
    value = filters.CharFilter(field_name='value', lookup_expr='icontains')

    class Meta:
        model = SystemConfig
        fields = ['pk', 'is_active', 'key', 'inherit', 'access', 'value', 'description']


class SystemConfigViewSet(BaseModelSet, InvalidConfigCacheAction, ImportExportDataAction):
    """系统配置"""
    queryset = SystemConfig.objects.all()
    serializer_class = SystemConfigSerializer
    ordering_fields = ['created_time']
    filterset_class = SystemConfigFilter

    @extend_schema(request=None, responses=get_default_response_schema())
    def destroy(self, request, *args, **kwargs):
        """删除{cls}并清理缓存"""
        self.invalid(request, *args, **kwargs)
        return super().destroy(request, *args, **kwargs)


class UserPersonalConfigFilter(SystemConfigFilter):
    pk = filters.UUIDFilter(field_name='id')
    username = filters.CharFilter(field_name='owner__username')
    owner_id = PkMultipleFilter(input_type='api-search-user')

    class Meta:
        model = UserPersonalConfig
        fields = ['pk', 'is_active', 'key', 'access', 'username', 'owner_id', 'value', 'description']


class UserPersonalConfigViewSet(SystemConfigViewSet):
    """用户配置"""
    queryset = UserPersonalConfig.objects.all()
    serializer_class = UserPersonalConfigSerializer
    ordering_fields = ['created_time']
    filterset_class = UserPersonalConfigFilter
    import_data_serializer_class = UserPersonalConfigExportImportSerializer
    export_data_serializer_class = UserPersonalConfigExportImportSerializer
