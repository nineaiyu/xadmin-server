#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : config
# author : ly_13
# date : 8/10/2024

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.core.config import SysConfig, UserConfig
from common.core.credentials import encrypt_setting_value
from common.core.fields import BasePrimaryKeyRelatedField
from common.core.serializers import BaseModelSerializer
from common.fields.utils import input_wrapper
from common.utils import get_logger
from system.models import SystemConfig, UserInfo, UserPersonalConfig
from system.utils.oauth import validate_providers

logger = get_logger(__name__)


# 系统配置写入侧校验分发表：坏配置在保存时挡住，而不是运行时才炸。
# 校验函数返回归一化后的 value（一并持久化）。
CONFIG_KEY_VALIDATORS = {
    # 第三方登录 provider 列表（含钉钉/企微/飞书 flavor）
    "OAUTH_PROVIDERS": lambda value: validate_providers(value),
}


class SystemConfigSerializer(BaseModelSerializer):
    class Meta:
        model = SystemConfig
        fields = ["pk", "key", "value", "cache_value", "is_active", "inherit", "access", "description", "created_time"]
        read_only_fields = ["pk"]
        fields_unexport = ["cache_value"]  # 导入导出文件时，忽略该字段

    def validate(self, attrs):
        validator = CONFIG_KEY_VALIDATORS.get(attrs.get("key"))
        if validator and "value" in attrs:
            try:
                attrs["value"] = validator(attrs["value"])
            except DjangoValidationError as exc:
                raise ValidationError(exc.messages) from exc
        return attrs

    cache_value = input_wrapper(serializers.SerializerMethodField)(
        read_only=True, label=_("Config cache value"), input_type="json"
    )

    def create(self, validated_data):
        """写入前加密敏感键的值内字段（凭据治理；非敏感键原样）。"""
        if "value" in validated_data:
            validated_data["value"] = encrypt_setting_value(validated_data.get("key"), validated_data["value"])
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if "value" in validated_data:
            key = validated_data.get("key") or getattr(instance, "key", "")
            validated_data["value"] = encrypt_setting_value(key, validated_data["value"])
        return super().update(instance, validated_data)

    @extend_schema_field(serializers.JSONField)
    def get_cache_value(self, obj):
        return SysConfig.get_value(obj.key)


class UserPersonalConfigExportImportSerializer(SystemConfigSerializer):
    class Meta:
        model = UserPersonalConfig
        fields = ["pk", "value", "key", "is_active", "created_time", "description", "cache_value", "owner", "access"]
        read_only_fields = ["pk"]
        extra_kwargs = {"owner": {"attrs": ["pk", "username"], "required": True}}


class UserPersonalConfigSerializer(SystemConfigSerializer):
    class Meta:
        model = UserPersonalConfig
        fields = [
            "pk",
            "config_user",
            "owner",
            "key",
            "value",
            "cache_value",
            "is_active",
            "access",
            "description",
            "created_time",
        ]
        read_only_fields = ["pk", "owner"]
        extra_kwargs = {"owner": {"attrs": ["pk", "username"], "read_only": True, "format": "{username}"}}

    config_user = BasePrimaryKeyRelatedField(
        write_only=True, many=True, queryset=UserInfo.objects, label=_("Users"), input_type="api-search-user"
    )

    def create(self, validated_data):
        config_user = validated_data.pop("config_user", [])
        owner = validated_data.pop("owner", None)
        instance = None
        if not config_user and not owner:
            raise ValidationError(_("User cannot be null"))
        if owner:
            config_user.append(owner)
        for owner in config_user:
            validated_data["owner"] = owner
            instance = super().create(validated_data)
        return instance

    def update(self, instance, validated_data):
        validated_data.pop("config_user", None)
        return super().update(instance, validated_data)

    @extend_schema_field(serializers.JSONField)
    def get_cache_value(self, obj):
        return UserConfig(obj.owner).get_value(obj.key)
