#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : websocket
# author : ly_13
# date : 3/27/2025

from common.core.filter import BaseFilterSet, PkMultipleFilter
from common.core.modelset import ListDeleteModelSet, OnlyExportDataAction
from common.core.pagination import DynamicPageNumber
from message.utils import get_online_info, send_logout_msg
from system.models import UserLoginLog
from system.serializers.log import UserOnlineSerializer


class UserOnlineFilter(BaseFilterSet):
    creator_id = PkMultipleFilter(input_type='api-search-user')

    class Meta:
        model = UserLoginLog
        fields = ['creator_id']


class UserOnlineViewSet(ListDeleteModelSet, OnlyExportDataAction):
    """websocket在线日志"""
    queryset = UserLoginLog.objects.filter(login_type=UserLoginLog.LoginTypeChoices.WEBSOCKET).all()
    serializer_class = UserOnlineSerializer
    pagination_class = DynamicPageNumber(1000)
    filterset_class = UserOnlineFilter
    ordering_fields = ['created_time']

    def get_queryset(self):
        online_user_pks, online_user_sockets = get_online_info()
        return self.queryset.filter(creator_id__in=online_user_pks, channel_name__in=online_user_sockets)

    def perform_destroy(self, instance):
        send_logout_msg(instance.creator_id, [instance.channel_name])
        return True
