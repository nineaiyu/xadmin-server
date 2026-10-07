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
from common.core.permission_meta import shared_list_action
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from system.models import SystemConfig, UserPersonalConfig
from system.serializers.config import (
    SystemConfigSerializer,
    UserPersonalConfigExportImportSerializer,
    UserPersonalConfigSerializer,
    registered_config_key_types,
)
from system.utils.platform.modelset import InvalidConfigCacheAction

logger = get_logger(__name__)


class SystemConfigFilter(BaseFilterSet):
    pk = filters.UUIDFilter(field_name="id")
    key = filters.CharFilter(field_name="key", lookup_expr="icontains")
    value = filters.CharFilter(field_name="value", lookup_expr="icontains")

    class Meta:
        model = SystemConfig
        fields = ["pk", "is_active", "key", "inherit", "access", "value", "description"]


class SystemConfigViewSet(BaseModelSet, InvalidConfigCacheAction, ImportExportDataAction):
    """系统配置"""

    queryset = SystemConfig.objects.all()
    serializer_class = SystemConfigSerializer
    ordering_fields = ["created_time"]
    filterset_class = SystemConfigFilter

    @extend_schema(request=None, responses=get_default_response_schema())
    def destroy(self, request, *args, **kwargs):
        """删除{cls}并清理缓存"""
        # 一次取实例复用：先清缓存再删除（父类 destroy 会再次 get_object，这里等价展开）
        instance = self.get_object()
        self._invalidate_config_cache(instance)
        self.perform_destroy(instance)
        return ApiResponse()

    @shared_list_action(methods=["get"], detail=False, url_path="registered-keys")
    def registered_keys(self, request, *args, **kwargs):
        """注册配置键清单（键名 + 期望值类型名）：配置页键枚举提示的数据源。"""
        return ApiResponse(data={"keys": [{"key": k, "type": t} for k, t in registered_config_key_types().items()]})


class UserPersonalConfigFilter(SystemConfigFilter):
    pk = filters.UUIDFilter(field_name="id")
    username = filters.CharFilter(field_name="owner__username")
    owner_id = PkMultipleFilter(input_type="api-search-user")

    class Meta:
        model = UserPersonalConfig
        fields = ["pk", "is_active", "key", "access", "username", "owner_id", "value", "description"]


class UserPersonalConfigViewSet(SystemConfigViewSet):
    """用户配置"""

    queryset = UserPersonalConfig.objects.all()
    serializer_class = UserPersonalConfigSerializer
    ordering_fields = ["created_time"]
    filterset_class = UserPersonalConfigFilter
    import_data_serializer_class = UserPersonalConfigExportImportSerializer
    export_data_serializer_class = UserPersonalConfigExportImportSerializer
