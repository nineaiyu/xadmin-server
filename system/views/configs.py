#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : configs
# author : ly_13
# date : 3/14/2024


from common.core.auth import auth_required
from common.core.config import UserConfig, SysConfig
from common.core.modelset import OwnerModelSet
from common.core.response import ApiResponse
from system.models import UserPersonalConfig
from system.utils.serializer import UserPersonalConfigSerializer


class ConfigsView(OwnerModelSet):
    queryset = UserPersonalConfig.objects.filter(is_active=True)
    serializer_class = UserPersonalConfigSerializer
    ordering_fields = ['created_time']
    lookup_field = 'key'
    permission_classes = []
    authentication_classes = []

    def list(self, request, *args, **kwargs):
        return ApiResponse()

    def retrieve(self, request, *args, **kwargs):
        value_key = self.kwargs[self.lookup_field]
        if value_key:
            if request.user and request.user.is_authenticated:
                site_config = UserConfig(request.user).get_value(value_key, ignore_access=False)
            else:
                site_config = SysConfig.get_value(value_key, ignore_access=False)
            if site_config:
                if not isinstance(site_config, dict):
                    site_config = {'value': site_config, 'key': self.kwargs[self.lookup_field]}
                return ApiResponse(**site_config, auth=f"{request.user}")
        return ApiResponse(config={}, auth=f"{request.user}")

    @auth_required
    def update(self, request, *args, **kwargs):
        value_key = self.kwargs[self.lookup_field]
        if value_key:
            if self.queryset.filter(key=value_key, access=True).count():
                UserConfig(request.user).set_value(value_key, request.data, is_active=True)
        return self.retrieve(request, *args, **kwargs)

    @auth_required
    def destroy(self, request, *args, **kwargs):
        value_key = self.kwargs[self.lookup_field]
        if value_key:
            UserConfig(request.user).del_value(value_key)
        return self.retrieve(request, *args, **kwargs)
