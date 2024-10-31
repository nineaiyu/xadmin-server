#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : configs
# author : ly_13
# date : 3/14/2024
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.viewsets import GenericViewSet

from common.core.auth import auth_required
from common.core.config import UserConfig, SysConfig
from common.core.filter import OwnerUserFilter
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models import UserPersonalConfig
from system.serializers.config import UserPersonalConfigSerializer


def config_response_schema():
    return get_default_response_schema({'config': build_object_type(), 'auth': build_basic_type(OpenApiTypes.STR)})


class ConfigsViewSet(GenericViewSet):
    """配置信息"""
    queryset = UserPersonalConfig.objects.none()
    serializer_class = UserPersonalConfigSerializer
    ordering_fields = ['created_time']
    lookup_field = 'key'
    permission_classes = []
    filter_backends = [OwnerUserFilter]

    @extend_schema(responses=config_response_schema())
    def retrieve(self, request, *args, **kwargs):
        """获取{cls}"""
        value_key = self.kwargs[self.lookup_field]
        if value_key:
            if request.user and request.user.is_authenticated:
                config = UserConfig(request.user).get_value(value_key, ignore_access=False)
            else:
                config = SysConfig.get_value(value_key, ignore_access=False)
            if config is not None:
                if not isinstance(config, dict):
                    config = {'value': config, 'key': self.kwargs[self.lookup_field]}
                return ApiResponse(config=config, auth=f"{request.user}")
        return ApiResponse(config={}, auth=f"{request.user}")

    @extend_schema(responses=config_response_schema(), request=OpenApiRequest(build_object_type()))
    @auth_required
    def partial_update(self, request, *args, **kwargs):
        """更新{cls}"""
        value_key = self.kwargs[self.lookup_field]
        if value_key:
            config = UserConfig(request.user).get_value(value_key, ignore_access=False)
            if config is not None:
                if isinstance(config, dict):
                    config.update({key: request.data.get(key, value) for key, value in config.items()})
                else:
                    config = request.data
                UserConfig(request.user).set_value(value_key, config, is_active=True, access=True)
        return self.retrieve(request, *args, **kwargs)

    @extend_schema(responses=config_response_schema())
    @auth_required
    def destroy(self, request, *args, **kwargs):
        """删除{cls}"""
        value_key = self.kwargs[self.lookup_field]
        if value_key:
            UserConfig(request.user).del_value(value_key)
        return self.retrieve(request, *args, **kwargs)
