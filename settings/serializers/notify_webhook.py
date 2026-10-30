#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Webhook 通知渠道配置序列化器。

字段名即配置名（BaseSettingViewSet 约定）；``WEBHOOK_SECRET`` write_only ⇒
Setting.encrypted=True 值级加密落库，retrieve 回显时自动剔除（与 IM 渠道
``*_SECRET`` 同口径）。URL 必填与开关形态对齐 IM 渠道页签（各页签只读写
自己的字段，非密文字段可 required=True）。
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from settings.serializers.contract import SettingSaveContractMixin


class WebhookNotifySettingSerializer(SettingSaveContractMixin, serializers.Serializer):
    """Webhook 转发端点：URL + 签名密钥"""

    WEBHOOK_ENABLED = serializers.BooleanField(
        default=False,
        label=_("Webhook notification"),
        help_text=_("Forward notifications to the configured webhook endpoint"),
    )
    WEBHOOK_URL = serializers.URLField(
        max_length=512,
        required=True,
        label=_("Webhook URL"),
        help_text=_("HTTPS endpoint that receives notification payloads"),
    )
    WEBHOOK_SECRET = serializers.CharField(
        max_length=256,
        required=False,
        allow_blank=True,
        write_only=True,
        label=_("Webhook secret"),
        help_text=_("HMAC-SHA256 signing secret; encrypted at rest and never returned"),
    )
