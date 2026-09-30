#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""外部 MCP 服务器序列化器：连接配置 CRUD（令牌明文只进不出）。

- ``url`` 写入侧过出站守卫（https 强制 / http 仅 loopback 或白名单）；
- ``auth_token`` 明文进 → signer 加密落库；回显只给 ``auth_token_set`` 布尔；
- ``allowed_tools`` 为调用白名单（空 = 全部禁止，fail-closed），写入侧去重收敛。
"""

import re

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from ai.models.mcp import McpServer
from ai.utils.mcp_client import validate_server_url
from common.base.utils import signer
from common.core.serializers import BaseModelSerializer
from common.core.validation import trim_required
from system.services import DisplayRelatedField

MAX_ALLOWED_TOOLS = 100
MAX_TOOL_NAME_LENGTH = 128
#: HTTP 头名（RFC 7230 token）
HEADER_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+\-.^_`|~]+$")


def _encrypt_token(value: str) -> str:
    value = (value or "").strip()
    return signer.encrypt(value.encode("utf-8")).decode("utf-8") if value else ""


class McpServerSerializer(BaseModelSerializer):
    """外部 MCP 服务器：auth_token 明文进 → 加密存；回显只给 auth_token_set 布尔。"""

    creator = DisplayRelatedField(
        read_only=True, allow_null=True, label=_("Creator"), label_builder=lambda v: v.username
    )
    auth_token = serializers.CharField(
        required=False, allow_blank=True, write_only=True, max_length=512, label=_("Auth token")
    )
    auth_token_set = serializers.SerializerMethodField(label=_("Auth token set"))

    class Meta:
        model = McpServer
        fields = [
            "pk",
            "name",
            "url",
            "auth_header",
            "auth_token",
            "auth_token_set",
            "timeout",
            "allowed_tools",
            "enabled",
            "tools_snapshot",
            "last_synced_time",
            "last_sync_error",
            "remark",
            "creator",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["auth_token_set", "tools_snapshot", "last_synced_time", "last_sync_error"]
        table_fields = [
            "name",
            "url",
            "enabled",
            "last_synced_time",
            "last_sync_error",
            "remark",
            "updated_time",
        ]

    def get_auth_token_set(self, obj) -> bool:
        return bool(obj.auth_token)

    def validate_name(self, value):
        return trim_required(value, _("Server name is required"))

    def validate_url(self, value):
        try:
            return validate_server_url(value)
        except Exception as exc:  # ValidationError → DRF 字段错误
            raise serializers.ValidationError(_error_messages(exc)) from exc

    def validate_timeout(self, value):
        if value is None:
            return 30
        if not 5 <= int(value) <= 120:
            raise serializers.ValidationError(_("Timeout must be between 5 and 120 seconds"))
        return int(value)

    def validate_auth_header(self, value):
        header = (value or "").strip()
        if header and not HEADER_RE.match(header):
            raise serializers.ValidationError(_("Auth header must be a valid HTTP header name"))
        return header

    def validate_allowed_tools(self, value):
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Allowed tools must be a list"))
        names: list[str] = []
        for item in value:
            name = str(item or "").strip()
            if not name:
                continue
            if len(name) > MAX_TOOL_NAME_LENGTH:
                raise serializers.ValidationError(_("Tool name is too long"))
            if name not in names:
                names.append(name)
        if len(names) > MAX_ALLOWED_TOOLS:
            raise serializers.ValidationError(_("Too many allowed tools (max {})").format(MAX_ALLOWED_TOOLS))
        return names

    def create(self, validated_data):
        token = validated_data.pop("auth_token", None)
        if token is not None:
            validated_data["auth_token"] = _encrypt_token(token)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        token = validated_data.pop("auth_token", None)
        if token is not None:
            validated_data["auth_token"] = _encrypt_token(token)
        return super().update(instance, validated_data)


def _error_messages(exc) -> list:
    messages = getattr(exc, "messages", None)
    if messages:
        return [str(item) for item in messages]
    return [str(exc)]
