#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""在线会话序列化器（identity 域，自 system/serializers/log.py 拆出）。"""

from common.core.fields import DictChoiceField
from common.core.serializers import BaseModelSerializer
from identity.consts import LoginTypeChoices
from identity.models import UserSession


class UserSessionSerializer(BaseModelSerializer):
    """在线会话（统一数据源）：WS 会话 + 纯 HTTP 会话。

    字段与旧版 UserOnlineSerializer（UserLoginLog）保持同名兼容，前端零改动；
    行主键从登录日志 pk 变为 UserSession pk（行维度「下线」按会话失效）。
    """

    # 登录类型字典化（与登录日志页同口径：字典未配置回退枚举，merge 保写入兼容）
    login_type = DictChoiceField(
        dict_code="login_type",
        fallback_choices=LoginTypeChoices.choices,
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
