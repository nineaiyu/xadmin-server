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
from message.services import get_online_users_layers
from system.models import UserLoginLog, OperationLog, UserSession
from system.serializers.fields import DictChoiceField

logger = get_logger(__name__)


class OperationLogSerializer(BaseModelSerializer):
    class Meta:
        model = OperationLog
        fields = [
            "pk",
            "module",
            "creator",
            "ipaddress",
            "path",
            "method",
            "browser",
            "system",
            "request_uuid",
            "exec_time",
            "response_code",
            "status_code",
            "body",
            "response_result",
            # 字段级审计 diff 进详情/导出（表格列仍保持精简）
            "changes",
            "created_time",
        ]

        table_fields = [
            "pk",
            "module",
            "creator",
            "ipaddress",
            "path",
            "method",
            "browser",
            "system",
            "exec_time",
            "status_code",
            "created_time",
        ]
        read_only_fields = ["pk"] + list(set([x.name for x in OperationLog._meta.fields]))
        extra_kwargs = {"creator": {"attrs": ["pk", "username"], "read_only": True, "format": "{username}"}}

    response_result = serializers.JSONField()
    body = serializers.JSONField()
    changes = serializers.JSONField(required=False, allow_null=True)


class LoginLogSerializer(BaseModelSerializer):
    class Meta:
        model = UserLoginLog
        fields = [
            "pk",
            "creator",
            "ipaddress",
            "city",
            "online",
            "channel_name",
            "login_type",
            "browser",
            "system",
            "agent",
            "status",
            "created_time",
        ]
        table_fields = [
            "pk",
            "creator",
            "ipaddress",
            "city",
            "online",
            "channel_name",
            "login_type",
            "browser",
            "system",
            "status",
            "created_time",
        ]
        read_only_fields = ["pk", "creator"]
        extra_kwargs = {"creator": {"attrs": ["pk", "username"], "read_only": True, "format": "{username}"}}

    online = serializers.SerializerMethodField(read_only=True, label=_("Online"))
    # 登录类型字典化：管理员可在数据字典 login_type 维护文案/颜色；merge 模式
    # 保证字典只配部分选项时，其余枚举值（写入路径 save_login_log）不受影响
    login_type = DictChoiceField(
        dict_code="login_type",
        fallback_choices=UserLoginLog.LoginTypeChoices.choices,
        value_cast=int,
        merge_fallback=True,
    )

    @extend_schema_field(serializers.IntegerField)
    def get_online(self, obj):
        if UserLoginLog.LoginTypeChoices.WEBSOCKET == obj.login_type:
            if not obj.creator:
                return -1
            # 以整页 creator 为单位批量查询在线 layers，结果缓存在 context 中（同页同一 creator 的多条日志可复用）
            if "login_log_online_layers" not in self.context:
                pks = [instance.creator.pk for instance in self.get_page_instances(obj) if instance.creator]
                self.context["login_log_online_layers"] = get_online_users_layers(pks)
            return obj.channel_name in self.context["login_log_online_layers"].get(obj.creator.pk, [])
        return -1


class UserLoginLogSerializer(LoginLogSerializer):
    class Meta:
        model = UserLoginLog
        fields = ["created_time", "status", "agent", "city", "login_type", "system", "browser", "ipaddress"]
        read_only_fields = [x.name for x in UserLoginLog._meta.fields]


class UserSessionSerializer(BaseModelSerializer):
    """在线会话（统一数据源）：WS 会话 + 纯 HTTP 会话。

    字段与旧版 UserOnlineSerializer（UserLoginLog）保持同名兼容，前端零改动；
    行主键从登录日志 pk 变为 UserSession pk（行维度「下线」按会话失效）。
    """

    # 登录类型字典化（与登录日志页同口径：字典未配置回退枚举，merge 保写入兼容）
    login_type = DictChoiceField(
        dict_code="login_type",
        fallback_choices=UserLoginLog.LoginTypeChoices.choices,
        value_cast=int,
        merge_fallback=True,
    )

    class Meta:
        model = UserSession
        fields = [
            "pk",
            "creator",
            "channel_name",
            "login_type",
            "agent",
            "city",
            "system",
            "browser",
            "ipaddress",
            "status",
            "last_active",
            "created_time",
        ]
        table_fields = [
            "pk",
            "creator",
            "ipaddress",
            "city",
            "channel_name",
            "login_type",
            "browser",
            "system",
            "status",
            "last_active",
            "created_time",
        ]
        read_only_fields = ["pk", "creator"]
        extra_kwargs = {"creator": {"attrs": ["pk", "username"], "read_only": True, "format": "{username}"}}
