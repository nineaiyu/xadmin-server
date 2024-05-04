#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : configs
# author : ly_13
# date : 3/14/2024

from common.core.auth import auth_required
from common.core.config import UserConfig, SysConfig
from common.core.filter import OwnerUserFilter
from common.core.modelset import OwnerModelSet
from common.core.response import ApiResponse
from system.models import UserPersonalConfig
from system.utils.serializer import UserPersonalConfigSerializer


class ConfigsView(OwnerModelSet):
    """配置信息"""
    queryset = UserPersonalConfig.objects.filter(is_active=True)
    serializer_class = UserPersonalConfigSerializer
    ordering_fields = ['created_time']
    lookup_field = 'key'
    permission_classes = []
    filter_backends = [OwnerUserFilter]

    def retrieve(self, request, *args, **kwargs):
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

    @auth_required
    def update(self, request, *args, **kwargs):
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

    @auth_required
    def destroy(self, request, *args, **kwargs):
        value_key = self.kwargs[self.lookup_field]
        if value_key:
            UserConfig(request.user).del_value(value_key)
        return self.retrieve(request, *args, **kwargs)
