#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : log
# author : ly_13
# date : 8/10/2024
import json
from typing import Any

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.utils import encoders

from audit.models import OperationLog, UserLoginLog
from audit.utils.mask import mask_operation_changes
from common.core.fields import DictChoiceField, LabeledChoiceField
from common.core.oplog_recorder import _log_field_limit, desensitize_payload
from common.core.serializers import BaseModelSerializer
from common.utils import get_logger
from message.services import get_online_users_layers

logger = get_logger(__name__)


# 日志大 JSON 字段的有界预览上限：三字段落库时已按 OPERATION_LOG_FIELD_MAX 截断，
# 输出侧再收一道有界预览（list / retrieve / export 同级），避免详情与导出文件
# 成为「全站请求/响应正文」的整包出口（LOG-P1-01）
OPERATION_LOG_LIST_PREVIEW_MAX = 500


# 值语义敏感端点：请求体只有 {"value": ...}（敏感键在上游行/URL），写入侧按
# 序列化器上下文登记脱敏；存量行没有登记，读侧按路径兜底重脱敏
VALUE_ONLY_SENSITIVE_PATHS = ("/api/system/config/system", "/api/system/config/personal")


def _bounded_log_body(value: Any, path: Any = "") -> Any:
    """日志正文读侧收敛：重跑脱敏 + 与列表同级的有界预览。

    落库是 JSON 文本（写入侧已脱敏）：解析成功按结构重跑一次脱敏——覆盖存量行
    在旧写入口径下漏掩的明文（value / 标量形态敏感值，见 LOG-P1-02）；配置写入
    端点额外按值语义掩掉 value/detail（存量行无请求级登记）。解析失败按原文
    处理。再按预览上限截断。任何一步失败回落原文截断，不让坏行把
    retrieve / 导出打挂。
    """
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None:
            # 值语义兜底只收「无 key 载体」的配置体：带 key 的请求由 key 名判定
            # （SITE_TITLE 这类非敏感配置项不能被按路径一刀切掩掉）
            value_only = (
                str(path or "").startswith(VALUE_ONLY_SENSITIVE_PATHS)
                and isinstance(parsed, dict)
                and "key" not in parsed
            )
            value_fields = ("value", "detail") if value_only else ()
            try:
                value = json.dumps(desensitize_payload(parsed, value_fields), cls=encoders.JSONEncoder, default=str)
            except Exception:  # noqa: BLE001 重脱敏失败回落原文（仍有截断兜底）
                logger.warning("operation log body re-desensitize failed", exc_info=True)
    if isinstance(value, str) and len(value) > OPERATION_LOG_LIST_PREVIEW_MAX:
        return value[:OPERATION_LOG_LIST_PREVIEW_MAX]
    return value


class OperationLogSerializer(BaseModelSerializer):
    class Meta:
        model = OperationLog
        fields = [
            "pk",
            "module",
            "model_label",
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

    def to_representation(self, instance: Any) -> Any:
        """changes 按源模型规则与查看者角色脱敏（超管/原文通道原样返回）。

        body / response_result 读侧收敛（LOG-P1-01）：retrieve / 导出与列表同级
        ——先重跑一次脱敏（存量行旧口径漏掩的 value/标量敏感值在此兜住），再
        截断到有界预览。详情与导出文件不再提供全量正文出口。
        """
        data = super().to_representation(instance)
        changes = data.get("changes")
        if changes:
            data["changes"] = mask_operation_changes(
                changes,
                getattr(instance, "model_label", ""),
                getattr(self, "request", None),
                getattr(getattr(self, "request", None), "user", None),
            )
        for name in ("body", "response_result"):
            # 响应体没有「值语义」漏项，路径兜底只对请求体生效
            data[name] = _bounded_log_body(data.get(name), getattr(instance, "path", "") if name == "body" else "")
        return data


class OperationLogListSerializer(OperationLogSerializer):
    """操作日志列表序列化器：大 JSON 字段降为有界预览（列表载荷轻量化）。

    列表表格列不渲染 body / response_result，但行数据仍被前端复用：
    详情抽屉直接渲染列表行（本视图集历史上无 retrieve），「变更历史」弹窗
    逐行读取 changes 渲染 old/new 对照——因此 changes 保留全量不裁剪；
    body / response_result 只保留预览，截断时附 ``*_truncated`` 标记
    （键恒在，向后兼容）。截断与脱敏由基类 to_representation 统一承载，
    retrieve / 导出与列表同级（LOG-P1-01）。
    """

    body_truncated = serializers.SerializerMethodField(label=_("Request body truncated"))
    response_result_truncated = serializers.SerializerMethodField(label=_("Response result truncated"))
    body_dropped = serializers.SerializerMethodField(label=_("Request body dropped"))
    response_result_dropped = serializers.SerializerMethodField(label=_("Response result dropped"))

    class Meta(OperationLogSerializer.Meta):
        fields = [
            *OperationLogSerializer.Meta.fields,
            "body_truncated",
            "response_result_truncated",
            "body_dropped",
            "response_result_dropped",
        ]
        table_fields = OperationLogSerializer.Meta.table_fields

    def get_body_truncated(self, obj: Any) -> bool:
        return isinstance(obj.body, str) and len(obj.body) > OPERATION_LOG_LIST_PREVIEW_MAX

    def get_response_result_truncated(self, obj: Any) -> bool:
        return isinstance(obj.response_result, str) and len(obj.response_result) > OPERATION_LOG_LIST_PREVIEW_MAX

    def get_body_dropped(self, obj: Any) -> bool:
        """OPERATION_LOG_FIELD_MAX=0 时正文按配置不落库，不是「没有正文」。"""
        return _log_field_limit() == 0 and not obj.body

    def get_response_result_dropped(self, obj: Any) -> bool:
        return _log_field_limit() == 0 and not obj.response_result


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
    def get_online(self, obj: Any) -> int:
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
