#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SCIM 目录同步设置序列化器（SystemConfig 存储，键名与运行期配置同名）。

与 `Setting` 体系的设置页不同，SCIM 的消费方 `SysConfig` 读取的是
`SystemConfig`；这里按同一批键名做 typed 表单，避免「保存到 Setting 但运行期
读不到」的第二套存储。
"""

import re
from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.fields import PasswordField
from settings.serializers.contract import SettingSaveContractMixin

# SimpleRateThrottle 速率串：数字/单位，或空/0 表示不限
RATE_LIMIT_RE = re.compile(r"^\d+/(sec|min|hour|day)$")


class ScimSettingSerializer(SettingSaveContractMixin, serializers.Serializer):
    SCIM_ENABLED = serializers.BooleanField(
        required=False,
        default=False,
        label=_("SCIM directory sync"),
        help_text=_("Enable the SCIM 2.0 endpoints for IdP-driven user and group provisioning"),
    )
    SCIM_TOKEN = PasswordField(
        required=False,
        allow_blank=True,
        write_only=True,
        max_length=512,
        label=_("SCIM bearer token"),
        help_text=_(
            "Encrypted at rest and never returned by the API; submit empty to keep the current token. "
            "Generate a random token below if you do not have one"
        ),
    )
    SCIM_RATE_LIMIT = serializers.CharField(
        required=False,
        allow_blank=True,
        default="600/min",
        max_length=32,
        label=_("SCIM rate limit"),
        help_text=_("Credential-level rate limit, e.g. 600/min; empty or 0 means unlimited"),
    )
    SCIM_DEFAULT_ROLE_CODE = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=64,
        label=_("Default role code"),
        help_text=_("Role assigned to users created by SCIM; empty means no role"),
    )
    SCIM_TOKEN_CONFIGURED = serializers.BooleanField(
        read_only=True,
        label=_("SCIM token configured"),
    )

    def validate_SCIM_RATE_LIMIT(self, value: str) -> str:
        value = (value or "").strip()
        if not value or value == "0":
            return value
        if not RATE_LIMIT_RE.match(value):
            raise serializers.ValidationError(_("Invalid rate limit: use a value like 600/min, or leave empty"))
        return value

    def validate_SCIM_DEFAULT_ROLE_CODE(self, value: str) -> str:
        value = (value or "").strip()
        if not value:
            return ""
        from identity.models import UserRole

        if not UserRole.objects.filter(code=value, deleted_at__isnull=True).exists():
            raise serializers.ValidationError(_("Unknown role code: {}").format(value))
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        enabled = attrs.get("SCIM_ENABLED")
        if enabled is None:
            enabled = bool(self.context.get("current_values", {}).get("SCIM_ENABLED"))
        token = attrs.get("SCIM_TOKEN")
        token_configured = self.context.get("current_values", {}).get("SCIM_TOKEN_CONFIGURED")
        if enabled and not token and not token_configured:
            raise serializers.ValidationError({"SCIM_TOKEN": _("SCIM requires a token before it can be enabled")})
        return attrs
