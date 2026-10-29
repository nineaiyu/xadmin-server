#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Webhook 订阅与投递序列化器。"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.core.auth.credentials import SENSITIVE_KEY_PATTERN
from common.core.oplog_recorder import desensitize_payload
from common.core.serializers import BaseModelSerializer
from task.models.webhook import WebhookDelivery, WebhookSubscription
from task.services.webhook import encrypt_secret, validate_events, validate_url


class WebhookSubscriptionSerializer(BaseModelSerializer):
    # 管理类资源：不参与字段权限裁剪（菜单权限点已控访问）
    ignore_field_permission = True

    has_secret = serializers.SerializerMethodField(label=_("Secret configured"))

    @extend_schema_field(serializers.BooleanField)
    def get_has_secret(self, obj: Any) -> bool:
        return bool(obj.secret)

    class Meta:
        model = WebhookSubscription
        fields = [
            "pk",
            "name",
            "url",
            "secret",
            "has_secret",
            "events",
            "description",
            "is_active",
            "last_failure",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["pk", "last_failure", "created_time", "updated_time"]
        extra_kwargs = {"secret": {"write_only": True}}
        # RePlusPage 列表列：secret 为 write_only 不进列，补充创建时间作审计锚点
        table_fields = ["name", "url", "events", "has_secret", "is_active", "last_failure", "created_time"]

    def validate_url(self, value: Any) -> Any:
        return validate_url(value)

    def validate_events(self, value: Any) -> Any:
        return validate_events(value)

    def validate_secret(self, value: Any) -> Any:
        if not value:
            # 更新时留空 = 沿用原密钥
            if self.instance:
                return self.instance.secret
            raise serializers.ValidationError(_("Secret is required"))
        return encrypt_secret(value)


#: 授权/会话类请求头不命中通用敏感键名模式，单独按头名收敛
SENSITIVE_HEADER_NAMES = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key"}


class WebhookDeliverySerializer(BaseModelSerializer):
    subscription_name = serializers.CharField(source="subscription.name", read_only=True, label=_("Subscription"))
    payload = serializers.JSONField(read_only=True, label=_("Payload"))
    request_headers = serializers.JSONField(read_only=True, label=_("Request headers"))

    ignore_field_permission = True

    def to_representation(self, instance: Any) -> dict[str, Any]:
        ret: dict[str, Any] = super().to_representation(instance)
        # 投递审计含事件载荷与请求头：按敏感键名统一掩码，避免查看者读到
        # 订阅密钥 / 授权头 / 用户敏感字段原文
        if isinstance(ret.get("payload"), dict):
            ret["payload"] = desensitize_payload(ret["payload"])
        headers = ret.get("request_headers")
        if isinstance(headers, dict):
            ret["request_headers"] = {
                key: (
                    "*" * len(str(value))
                    if str(key).lower() in SENSITIVE_HEADER_NAMES or SENSITIVE_KEY_PATTERN.search(str(key))
                    else value
                )
                for key, value in headers.items()
            }
        return ret

    class Meta:
        model = WebhookDelivery
        fields = [
            "pk",
            "subscription",
            "subscription_name",
            "event",
            "payload",
            "request_headers",
            "status",
            "attempt",
            "response_code",
            "response_body",
            "response_body_truncated",
            "duration",
            "next_retry_at",
            "created_time",
        ]
        read_only_fields = fields
        # 投递审计列：subscription 主键/next_retry_at 属内部字段不进列，只留审计关注项
        table_fields = [
            "created_time",
            "subscription_name",
            "event",
            "payload",
            "request_headers",
            "status",
            "attempt",
            "response_code",
            "response_body",
            "duration",
        ]
