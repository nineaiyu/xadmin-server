#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""个人访问令牌（PAT）序列化器。

明文 token 仅在创建时返回一次（get_token 读取实例上的临时属性，不落库）；
其余字段照常。属主由 pre_save 信号写入 creator，禁止客户端指定。
"""

import secrets

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.auth import hash_pat_token
from common.core.serializers import BaseModelSerializer
from system.models.token import PersonalAccessToken


class PersonalAccessTokenSerializer(BaseModelSerializer):
    # 明文令牌：仅创建响应携带一次，列表/详情恒为 None
    token = serializers.SerializerMethodField(label=_("Token"))
    token_prefix = serializers.CharField(label=_("Token prefix"), read_only=True)
    last_used_time = serializers.DateTimeField(label=_("Last used time"), read_only=True)
    # scope 清单：允许的路径前缀/正则（空 = 不限），update 允许改写
    scopes = serializers.JSONField(
        label=_("Scopes"),
        required=False,
        allow_null=True,
        help_text=_("List of allowed path prefixes or regexes; empty means unrestricted"),
    )

    class Meta:
        model = PersonalAccessToken
        fields = [
            "pk",
            "name",
            "token",
            "token_prefix",
            "scopes",
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

    def create(self, validated_data):
        # 明文仅此一次：pat_ 前缀 + 32 字节 URL 安全随机串
        raw_token = "pat_{}".format(secrets.token_urlsafe(32))
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
