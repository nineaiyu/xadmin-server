#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : log
# author : ly_13
# date : 8/10/2024
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from common.utils import get_logger
from message.utils import get_online_user_layers
from system.models import UserLoginLog, OperationLog

logger = get_logger(__name__)


class OperationLogSerializer(BaseModelSerializer):
    class Meta:
        model = OperationLog
        fields = [
            "pk", "module", "creator", "ipaddress", "path", "method", "browser", "system", "request_uuid", "exec_time",
            "response_code", "status_code", "body", "response_result", "created_time"
        ]

        table_fields = [
            "pk", "module", "creator", "ipaddress", "path", "method", "browser", "system", "exec_time", "status_code",
            "created_time"
        ]
        read_only_fields = ["pk"] + list(set([x.name for x in OperationLog._meta.fields]))
        extra_kwargs = {'creator': {'attrs': ['pk', 'username'], 'read_only': True, 'format': '{username}'}}

    response_result = serializers.JSONField()
    body = serializers.JSONField()


class LoginLogSerializer(BaseModelSerializer):
    class Meta:
        model = UserLoginLog
        fields = [
            'pk', 'creator', 'ipaddress', 'city', 'online', 'channel_name', 'login_type', 'browser', 'system', 'agent',
            'status', 'created_time'
        ]
        table_fields = [
            'pk', 'creator', 'ipaddress', 'city', 'online', 'channel_name', 'login_type', 'browser', 'system', 'status',
            'created_time'
        ]
        read_only_fields = ['pk', 'creator']
        extra_kwargs = {'creator': {'attrs': ['pk', 'username'], 'read_only': True, 'format': '{username}'}}

    online = serializers.SerializerMethodField(read_only=True, label=_("Online"))

    @extend_schema_field(serializers.IntegerField)
    def get_online(self, obj):
        if UserLoginLog.LoginTypeChoices.WEBSOCKET == obj.login_type:
            return obj.channel_name in get_online_user_layers(obj.creator.pk)
        return -1


class UserLoginLogSerializer(LoginLogSerializer):
    class Meta:
        model = UserLoginLog
        fields = ['created_time', 'status', 'agent', 'city', 'login_type', 'system', 'browser', 'ipaddress']
        read_only_fields = [x.name for x in UserLoginLog._meta.fields]


class UserOnlineSerializer(LoginLogSerializer):
    class Meta:
        model = UserLoginLog
        fields = ['pk', 'creator', 'channel_name', 'agent', 'city', 'system', 'browser', 'ipaddress', 'created_time']
        read_only_fields = [x.name for x in UserLoginLog._meta.fields]
        extra_kwargs = {'creator': {'attrs': ['pk', 'username'], 'read_only': True, 'format': '{username}'}}
