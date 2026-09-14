#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 助手配置序列化器：Setting 体系，category=ai。

API Key write_only ⇒ 值级加密落库、retrieve 回显自动剔除（G12 模式先行）。
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

    def validate(self, attrs):
        # 留白模型名收敛默认值，避免空配置静默失效
        if attrs.get("AI_MODEL") in (None, ""):
            attrs["AI_MODEL"] = "gpt-4o-mini"
        return attrs
