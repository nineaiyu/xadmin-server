#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : config
# author : ly_13
# date : 3/4/2024
from rest_framework.decorators import action

from common.core.config import UserConfig
from common.core.filter import OwnerUserFilter
from common.core.modelset import OwnerModelSet
from common.core.response import ApiResponse
from system.models import UserPersonalConfig
from system.utils.serializer import UserPersonalConfigSerializer


class UserConfigView(OwnerModelSet):
    queryset = UserPersonalConfig.objects.filter(is_active=True)
    serializer_class = UserPersonalConfigSerializer
    ordering_fields = ['created_time']
    filterset_class = OwnerUserFilter
    lookup_field = 'key'

    def list(self, request, *args, **kwargs):
        return ApiResponse()

    @action(methods=['get'], detail=False)
    def site(self, request, *args, **kwargs):
        site_config = UserConfig(request.user).get_value('WEB_SITE_CONFIG')
        if site_config and isinstance(site_config, dict):
            return ApiResponse(data=site_config)
        return ApiResponse(data={})
