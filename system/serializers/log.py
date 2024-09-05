#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : log
# author : ly_13
# date : 8/10/2024

import logging

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.core.fields import BasePrimaryKeyRelatedField, LabeledChoiceField
from common.core.serializers import BaseModelSerializer
from system.models import UserLoginLog, OperationLog

logger = logging.getLogger(__name__)


class OperationLogSerializer(BaseModelSerializer):
    class Meta:
        model = OperationLog
        fields = ["pk", "module", "creator", "ipaddress", "path", "method", "browser", "system",
                  "response_code", "status_code", "body", "response_result", "created_time"]

        table_fields = ["pk", "module", "creator", "ipaddress", "path", "method", "browser", "system",
                        "status_code", "created_time"]
        read_only_fields = ["pk"] + list(set([x.name for x in OperationLog._meta.fields]))

    creator = BasePrimaryKeyRelatedField(attrs=['pk', 'username'], read_only=True, label=_("User"))
    module = serializers.SerializerMethodField(label=_("Module"))

    @extend_schema_field(serializers.CharField)
    def get_module(self, obj):
        module_name = obj.module
        map_module_name = settings.API_MODEL_MAP.get(obj.path, None)
        if not module_name and map_module_name:
            return map_module_name
        return module_name


class UserLoginLogSerializer(BaseModelSerializer):
    class Meta:
        model = UserLoginLog
        fields = ['pk', 'creator', 'ipaddress', 'city', 'login_type', 'browser', 'system', 'agent', 'status',
                  'created_time']
        table_fields = ['pk', 'creator', 'ipaddress', 'city', 'login_type', 'browser', 'system', 'status',
                        'created_time']
        read_only_fields = ['pk', 'creator']

    creator = BasePrimaryKeyRelatedField(attrs=['pk', 'username'], read_only=True, label=_("User"))
    login_type = LabeledChoiceField(choices=UserLoginLog.LoginTypeChoices.choices,
                                    default=UserLoginLog.LoginTypeChoices.USERNAME, label=_("Login type"))
