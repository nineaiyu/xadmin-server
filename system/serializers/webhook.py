#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Webhook 订阅与投递序列化器。"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from system.models.webhook import WebhookDelivery, WebhookSubscription
from system.utils.webhook import encrypt_secret, validate_events, validate_url


class WebhookSubscriptionSerializer(BaseModelSerializer):
    # 管理类资源：不参与字段权限裁剪（菜单权限点已控访问）
    ignore_field_permission = True

    class Meta:
        model = WebhookSubscription
        fields = [
            "pk",
            "name",
            "url",
            "secret",
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
        table_fields = ["name", "url", "events", "is_active", "last_failure", "created_time"]

    def validate_url(self, value):
        return validate_url(value)

    def validate_events(self, value):
        return validate_events(value)

    def validate_secret(self, value):
        if not value:
            # 更新时留空 = 沿用原密钥
            if self.instance:
                return self.instance.secret
            raise serializers.ValidationError(_("Secret is required"))
        return encrypt_secret(value)


class WebhookDeliverySerializer(BaseModelSerializer):
    subscription_name = serializers.CharField(source="subscription.name", read_only=True, label=_("Subscription"))

    ignore_field_permission = True

    class Meta:
        model = WebhookDelivery
        fields = [
            "pk",
            "subscription",
            "subscription_name",
            "event",
            "status",
            "attempt",
            "response_code",
            "response_body",
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
            "status",
            "attempt",
            "response_code",
            "response_body",
            "duration",
        ]
