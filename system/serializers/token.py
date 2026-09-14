#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""个人访问令牌（PAT）序列化器。

明文 token 仅在创建时返回一次（get_token 读取实例上的临时属性，不落库）；
其余字段照常。属主由 pre_save 信号写入 creator，禁止客户端指定。
"""

import ipaddress
import secrets

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.auth import hash_pat_token
from common.core.serializers import BaseModelSerializer
from system.models.token import ApiApplication, PersonalAccessToken


class PersonalAccessTokenSerializer(BaseModelSerializer):
    # 明文令牌：仅创建响应携带一次，列表/详情恒为 None
    token = serializers.SerializerMethodField(label=_("Token"))
    token_prefix = serializers.CharField(label=_("Token prefix"), read_only=True)
    last_used_time = serializers.DateTimeField(label=_("Last used time"), read_only=True)
    # scope 清单：允许的路径前缀/正则（可带方法前缀，空 = 不限），update 允许改写
    scopes = serializers.JSONField(
        label=_("Scopes"),
        required=False,
        allow_null=True,
        help_text=_("List of allowed path prefixes or regexes; empty means unrestricted"),
    )
    # IP 白名单：单个 IP 或 CIDR 网段（空 = 不限来源 IP），update 允许改写
    ip_allowlist = serializers.JSONField(
        label=_("Ip allowlist"),
        required=False,
        allow_null=True,
        help_text=_("Allowed source IPs or CIDR networks; empty means unrestricted"),
    )

    class Meta:
        model = PersonalAccessToken
        fields = [
            "pk",
            "name",
            "token",
            "token_prefix",
            "scopes",
            "ip_allowlist",
            "description",
            "is_active",
            "expired_at",
            "last_used_time",
            "created_time",
            "updated_time",
        ]
        table_fields = [
            "pk",
            "name",
            "token_prefix",
            "scopes",
            "ip_allowlist",
            "is_active",
            "expired_at",
            "last_used_time",
            "description",
            "created_time",
        ]
        extra_kwargs = {
            "name": {"required": True, "allow_blank": False, "max_length": 128},
            "description": {"required": False, "allow_blank": True},
            "expired_at": {"required": False, "allow_null": True},
        }

    def get_token(self, obj):
        return getattr(obj, "_plain_token", None)

    def validate_scopes(self, value):
        """scope 清单清洗：字符串清单、去空白、去重；None/空 = 不限。"""
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Scopes must be a list of path strings"))
        cleaned = []
        for item in value:
            item = str(item or "").strip()
            if item and item not in cleaned:
                cleaned.append(item)
        return cleaned

    def validate_ip_allowlist(self, value):
        """IP 白名单清洗：去空白、去重、逐条校验 IP/CIDR 格式；None/空 = 不限。"""
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Ip allowlist must be a list of IPs or CIDR networks"))
        cleaned = []
        for item in value:
            entry = str(item or "").strip()
            if not entry:
                continue
            try:
                if "/" in entry:
                    ipaddress.ip_network(entry, strict=False)
                else:
                    ipaddress.ip_address(entry)
            except ValueError as exc:
                raise serializers.ValidationError(
                    _("Invalid IP or CIDR network: %(entry)s") % {"entry": entry}
                ) from exc
            if entry not in cleaned:
                cleaned.append(entry)
        return cleaned

    def create(self, validated_data):
        # 明文仅此一次：pat_ 前缀 + 32 字节 URL 安全随机串
        raw_token = f"pat_{secrets.token_urlsafe(32)}"
        validated_data["token_hash"] = hash_pat_token(raw_token)
        validated_data["token_prefix"] = raw_token[:12]
        instance = super().create(validated_data)
        instance._plain_token = raw_token
        return instance

    def update(self, instance, validated_data):
        # 凭证本体不可变：吊销走 is_active，续期走 expired_at，哈希/前缀禁止改写
        validated_data.pop("token_hash", None)
        validated_data.pop("token_prefix", None)
        return super().update(instance, validated_data)


class ApiApplicationSerializer(BaseModelSerializer):
    """开放平台应用序列化器。

    client_id / client_secret_prefix 只读（由服务端生成）；client_secret 与 callback_secret
    的明文仅在创建/重置响应中返回一次（视图层注入，不经本序列化器）。
    """

    class Meta:
        model = ApiApplication
        fields = [
            "pk",
            "name",
            "client_id",
            "client_secret_prefix",
            "scopes",
            "ip_allowlist",
            "rate_limit_per_minute",
            "callback_urls",
            "token_ttl_seconds",
            "is_active",
            "expired_at",
            "description",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["client_id", "client_secret_prefix"]
        table_fields = [
            "name",
            "client_id",
            "scopes",
            "rate_limit_per_minute",
            "is_active",
            "created_time",
        ]

    def validate_callback_urls(self, value):
        """回调地址写入校验：https 强制（loopback http 例外），复用 webhook 同口径。"""
        from system.utils.webhook import validate_url

        if not isinstance(value, list):
            raise serializers.ValidationError(_("Callback urls must be a list"))
        return [validate_url(url) for url in value]

    def validate_scopes(self, value):
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise serializers.ValidationError(_("Scopes must be a list of strings"))
        return [item.strip() for item in value if item.strip()]

    def validate_ip_allowlist(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Ip allowlist must be a list"))
        return [str(item).strip() for item in value if str(item).strip()]

    def validate_rate_limit_per_minute(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError(_("Rate limit cannot be negative"))
        return value
