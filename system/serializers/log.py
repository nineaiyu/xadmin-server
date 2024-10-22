#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : log
# author : ly_13
# date : 8/10/2024

from django.utils.translation import gettext_lazy as _

from common.core.fields import BasePrimaryKeyRelatedField, LabeledChoiceField
from common.core.serializers import BaseModelSerializer
from common.utils import get_logger
from system.models import UserLoginLog, OperationLog

logger = get_logger(__name__)


class OperationLogSerializer(BaseModelSerializer):
    class Meta:
        model = OperationLog
        fields = ["pk", "module", "creator", "ipaddress", "path", "method", "browser", "system", "request_uuid",
                  "exec_time", "response_code", "status_code", "body", "response_result", "created_time"]

        table_fields = ["pk", "module", "creator", "ipaddress", "path", "method", "browser", "system", "exec_time",
                        "status_code", "created_time"]
        read_only_fields = ["pk"] + list(set([x.name for x in OperationLog._meta.fields]))

    creator = BasePrimaryKeyRelatedField(attrs=['pk', 'username'], read_only=True, label=_("User"))


class LoginLogSerializer(BaseModelSerializer):
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


class UserLoginLogSerializer(LoginLogSerializer):
    class Meta:
        model = UserLoginLog
        fields = ['created_time', 'status', 'agent', 'city', 'login_type', 'system', 'browser', 'ipaddress']
        read_only_fields = [x.name for x in UserLoginLog._meta.fields]
