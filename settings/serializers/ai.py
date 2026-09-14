#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 助手配置序列化器：Setting 体系，category=ai。

API Key write_only ⇒ 值级加密落库、retrieve 回显自动剔除（G12 模式先行）。

多档案（AiProfile）为主通路后，本序列化器承载「无激活档案时」的回落配置：
凭据 + 采样/行为参数全集（与档案字段一一对应），None/留空 = 不下发走供应商默认。
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers


class AiAssistantSettingSerializer(serializers.Serializer):
    AI_ASSISTANT_ENABLED = serializers.BooleanField(
        default=False, label=_("AI assistant"), help_text=_("Enable the docs-based usage/development assistant")
    )
    AI_BASE_URL = serializers.CharField(
        max_length=256,
        required=False,
        allow_blank=True,
        label=_("Base URL"),
        help_text=_("OpenAI-compatible endpoint, e.g. https://api.deepseek.com/v1"),
    )
    AI_API_KEY = serializers.CharField(
        max_length=512,
        required=False,
        allow_blank=True,
        write_only=True,
        label=_("API Key"),
        help_text=_("Encrypted at rest; never returned by the API"),
    )
    AI_MODEL = serializers.CharField(
        max_length=128, required=False, allow_blank=True, label=_("Model"), help_text=_("e.g. deepseek-chat")
    )
    AI_TIMEOUT = serializers.IntegerField(default=60, min_value=5, max_value=300, label=_("Timeout (seconds)"))
    AI_NL_QUERY_ENABLED = serializers.BooleanField(
        default=False,
        label=_("NL query"),
        help_text=_("Enable natural-language dataset query (gated preview + execute, audited)"),
    )
    # 采样/行为参数（回落通路；与 AiProfile 档案字段同义）
    AI_TEMPERATURE = serializers.FloatField(
        required=False, allow_null=True, min_value=0, max_value=2, label=_("Temperature")
    )
    AI_MAX_TOKENS = serializers.IntegerField(required=False, allow_null=True, min_value=0, label=_("Max tokens"))
    AI_TOP_P = serializers.FloatField(required=False, allow_null=True, min_value=0, max_value=1, label=_("Top P"))
    AI_FREQUENCY_PENALTY = serializers.FloatField(
        required=False, allow_null=True, min_value=-2, max_value=2, label=_("Frequency penalty")
    )
    AI_PRESENCE_PENALTY = serializers.FloatField(
        required=False, allow_null=True, min_value=-2, max_value=2, label=_("Presence penalty")
    )
    AI_STOP = serializers.CharField(max_length=255, required=False, allow_blank=True, label=_("Stop sequences"))
    AI_SEED = serializers.IntegerField(required=False, allow_null=True, label=_("Seed"))
    AI_MAX_RETRIES = serializers.IntegerField(default=0, min_value=0, max_value=3, label=_("Max retries"))
    AI_CONTEXT_LIMIT = serializers.IntegerField(default=20, min_value=2, max_value=50, label=_("Context messages"))
    AI_PERSONA = serializers.CharField(max_length=2000, required=False, allow_blank=True, label=_("Persona"))

    def validate(self, attrs):
        # 留白模型名收敛默认值，避免空配置静默失效
        if attrs.get("AI_MODEL") in (None, ""):
            attrs["AI_MODEL"] = "gpt-4o-mini"
        return attrs
