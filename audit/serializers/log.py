#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : log
# author : ly_13
# date : 8/10/2024
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from audit.models import OperationLog, UserLoginLog
from common.core.fields import DictChoiceField, LabeledChoiceField
from common.core.serializers import BaseModelSerializer
from common.utils import get_logger
from message.services import get_online_users_layers

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
            "auth_type",
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
            "auth_type",
            "browser",
            "system",
            "exec_time",
            "status_code",
            "created_time",
        ]
        read_only_fields = ["pk"] + list(set([x.name for x in OperationLog._meta.fields]))
        extra_kwargs = {"creator": {"attrs": ["pk", "username"], "read_only": True, "format": "{username}"}}

    # 凭证类型：labeled_choice 下发 {value,label}，前端表格直接展示彩色/可读标签
    auth_type = LabeledChoiceField(choices=OperationLog.AuthType.choices, required=False, allow_null=True)
    response_result = serializers.JSONField()
    body = serializers.JSONField()
    changes = serializers.JSONField(required=False, allow_null=True)


# 列表行大 JSON 字段的预览上限：三字段落库时已按 OPERATION_LOG_FIELD_MAX 截断，
# 列表再收一道有界预览，避免整页载荷被请求/响应正文撑爆（详情/导出走全量口径）
OPERATION_LOG_LIST_PREVIEW_MAX = 500


class OperationLogListSerializer(OperationLogSerializer):
    """操作日志列表序列化器：大 JSON 字段降为有界预览（列表载荷轻量化）。

    列表表格列不渲染 body / response_result，但行数据仍被前端复用：
    详情抽屉直接渲染列表行（本视图集历史上无 retrieve），「变更历史」弹窗
    逐行读取 changes 渲染 old/new 对照——因此 changes 保留全量不裁剪；
    body / response_result 只保留预览，截断时附 ``*_truncated`` 标记
    （键恒在，向后兼容）。全量口径由 retrieve / 导出经 OperationLogSerializer 承载。
    """

    body_truncated = serializers.SerializerMethodField(label=_("Request body truncated"))
    response_result_truncated = serializers.SerializerMethodField(label=_("Response result truncated"))

    class Meta(OperationLogSerializer.Meta):
        fields = [*OperationLogSerializer.Meta.fields, "body_truncated", "response_result_truncated"]
        table_fields = OperationLogSerializer.Meta.table_fields

    def get_body_truncated(self, obj) -> bool:
        return isinstance(obj.body, str) and len(obj.body) > OPERATION_LOG_LIST_PREVIEW_MAX

    def get_response_result_truncated(self, obj) -> bool:
        return isinstance(obj.response_result, str) and len(obj.response_result) > OPERATION_LOG_LIST_PREVIEW_MAX

    def to_representation(self, instance):
        data = super().to_representation(instance)
        for name in ("body", "response_result"):
            value = data.get(name)
            if isinstance(value, str) and len(value) > OPERATION_LOG_LIST_PREVIEW_MAX:
                data[name] = value[:OPERATION_LOG_LIST_PREVIEW_MAX]
        return data


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
            "policy_result",
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
            "policy_result",
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
        """在线态三元取值：-1 = 不适用（非 WS 登录或无 creator），True/False = WS 会话是否在线。

        前端按 `{true: 在线, false: 离线, "-1": "/"}` 渲染；两侧口径保持一致。
        """
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
