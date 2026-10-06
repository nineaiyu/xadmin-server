#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : config
# author : ly_13
# date : 8/10/2024

import math

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.core.config import (
    BaseConfCache,
    ConfigCache,
    MessagePushConfCache,
    SysConfig,
    UserConfig,
    batch_user_config_values,
)
from common.core.credentials import encrypt_setting_value
from common.core.fields import BasePrimaryKeyRelatedField
from common.core.serializers import BaseModelSerializer
from common.fields.utils import input_wrapper
from common.injection import get_server_config
from common.utils import get_logger
from identity.services import UserInfo
from identity.utils.oauth import validate_providers
from system.models import SystemConfig, UserPersonalConfig

logger = get_logger(__name__)


# 系统配置写入侧校验分发表：坏配置在保存时挡住，而不是运行时才炸。
# 校验函数返回归一化后的 value（一并持久化）。
CONFIG_KEY_VALIDATORS = {
    # 第三方登录 provider 列表（含钉钉/企微/飞书 flavor）
    "OAUTH_PROVIDERS": lambda value: validate_providers(value),
}

# 注册键的期望类型表（写入期类型校验 + 受控收敛）：
# - 键面沿 MRO 收集 SysConfig 注册属性（与 conf 单源守护测试同口径），另补
#   系统配置页可写、但非 SysConfig property 的键（WEB_SITE_CONFIG 站点配置对象）；
# - 期望类型取 conf 默认值表（CONFIG.defaults，默认值单源）中该键默认值的类型，
#   与读取侧属性对数值键的 int()/float() 强转同型；推导不出可靠类型的键不入表，
#   写入放行不误伤；未注册键行为与现状一致（原样放行）。
# 数值型等类型化键的坏值由此在保存时挡住（400 + 明确文案），不再静默落库、
# 等到读取使用时才炸。
EXTRA_CONFIG_KEY_TYPES = {
    # 前端站点配置整体对象（非 SysConfig property，种子豁免键）
    "WEB_SITE_CONFIG": dict,
}

_CONFIG_VALUE_TYPES: dict[str, type] | None = None


def _registered_config_keys() -> set[str]:
    """沿 MRO 收集 SysConfig 注册键（property 名 = 配置键）。"""
    keys: set[str] = set()
    for cls in (BaseConfCache, MessagePushConfCache, ConfigCache):
        for klass in cls.__mro__:
            keys |= {name for name, value in vars(klass).items() if isinstance(value, property)}
    return keys


def _config_value_types() -> dict[str, type]:
    """注册键 → 期望类型（惰性构建并缓存）。"""
    global _CONFIG_VALUE_TYPES
    if _CONFIG_VALUE_TYPES is None:
        reliable_types = (bool, int, float, str, list, dict)
        defaults = type(get_server_config()).defaults
        types = {
            key: type(defaults[key]) for key in _registered_config_keys() if type(defaults.get(key)) in reliable_types
        }
        types.update(EXTRA_CONFIG_KEY_TYPES)
        _CONFIG_VALUE_TYPES = types
    return _CONFIG_VALUE_TYPES


def _type_error(key, expected_type: type) -> ValidationError:
    messages = {
        int: _("Config value for {} must be an integer"),
        float: _("Config value for {} must be a number"),
        bool: _("Config value for {} must be a boolean"),
        str: _("Config value for {} must be a string"),
        list: _("Config value for {} must be a JSON array"),
        dict: _("Config value for {} must be a JSON object"),
    }
    return ValidationError(messages[expected_type].format(key))


def _coerce_config_value(key, value, expected_type: type):
    """按期望类型收敛写入值，非法即 400。

    受控收敛口径：数值键接受等值字符串（"180" / "0.5"）与整数值浮点（180.0），
    布尔键接受 true/false（大小写不敏感）与 1/0，入库前统一转型为正确类型——
    与前端 JSON 编辑器提交的原生类型兼容，同时消除「字符串 false 落库后恒真」
    这类读取侧无法察觉的坏值；其余类型从严拒绝。
    """
    if expected_type is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.strip().lower() in ("true", "1"):
            return True
        if isinstance(value, str) and value.strip().lower() in ("false", "0"):
            return False
        raise _type_error(key, expected_type)
    if expected_type is int:
        # bool 是 int 子类：显式排除，True 不能当 1 写进数值配置
        if isinstance(value, bool):
            raise _type_error(key, expected_type)
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                pass
        raise _type_error(key, expected_type)
    if expected_type is float:
        if isinstance(value, bool):
            raise _type_error(key, expected_type)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                number = float(value.strip())
            except ValueError:
                raise _type_error(key, expected_type) from None
            # nan/inf 不是合法 JSON 值，落库只会把坏值带到读取侧
            if math.isfinite(number):
                return number
        raise _type_error(key, expected_type)
    if isinstance(value, expected_type):
        return value
    raise _type_error(key, expected_type)


def validate_config_value(key, value):
    """写入期校验注册键的值类型并做受控收敛；未注册键原样放行。"""
    expected_type = _config_value_types().get(key)
    if expected_type is None:
        return value
    return _coerce_config_value(key, value, expected_type)


class SystemConfigSerializer(BaseModelSerializer):
    class Meta:
        model = SystemConfig
        fields = ["pk", "key", "value", "cache_value", "is_active", "inherit", "access", "description", "created_time"]
        read_only_fields = ["pk"]
        fields_unexport = ["cache_value"]  # 导入导出文件时，忽略该字段

    def validate(self, attrs):
        # 键取请求值，缺省回退实例行键（PATCH 只改 value 时同样过校验）
        key = attrs.get("key") or getattr(self.instance, "key", None)
        if key and "value" in attrs:
            # 类型校验先行：自定义校验器拿到的是已收敛类型的值
            attrs["value"] = validate_config_value(key, attrs["value"])
            validator = CONFIG_KEY_VALIDATORS.get(key)
            if validator:
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
        """生效值：整页序列化时一次批量预取（context 记忆），单对象仍逐 key 读取。"""
        page = self.get_page_instances(obj)
        cached = self.context.get("_page_system_cache_values")
        if cached is None and len(page) > 1:
            cached = SysConfig.get_values([row.key for row in page])
            self.context["_page_system_cache_values"] = cached
        if cached is not None and obj.key in cached:
            return cached[obj.key]
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

    def _dedupe_users(self, users) -> list:
        """同一用户重复提交只建一条：否则第二次 create 撞 (owner, key) 唯一约束。"""
        seen, result = set(), []
        for user in users:
            pk = user.pk
            if pk in seen:
                continue
            seen.add(pk)
            result.append(user)
        return result

    def _check_conflicts(self, users, key) -> None:
        """冲突预检：任一用户已有同名 key 时给出含用户名的可读明细，而不是 IntegrityError 500。"""
        existing = UserPersonalConfig.objects.filter(owner__in=users, key=key).select_related("owner")
        usernames = [row.owner.username for row in existing]
        if usernames:
            raise ValidationError(
                _("Config key already exists for user(s): {}").format(", ".join(sorted(set(usernames))))
            )

    def create(self, validated_data):
        """批量建用户参数：事务包裹 + 冲突预检。

        原实现循环逐个 create：任一用户已有同名 key 即 IntegrityError 500，且
        前序用户已落库（部分写入）。现预查冲突返回可读错误（含冲突用户明细），
        并以事务保证「要么全部建成、要么一条不写」；残余并发竞态由 IntegrityError
        捕获转可读错误兜底（事务回滚，同样不落半批）。
        """
        from django.db import IntegrityError, transaction

        config_user = validated_data.pop("config_user", [])
        owner = validated_data.pop("owner", None)
        if not config_user and not owner:
            raise ValidationError(_("User cannot be null"))
        if owner:
            config_user.append(owner)
        users = self._dedupe_users(config_user)
        key = validated_data.get("key")
        try:
            with transaction.atomic():
                self._check_conflicts(users, key)
                instance = None
                for user in users:
                    validated_data["owner"] = user
                    instance = super().create(validated_data)
                return instance
        except IntegrityError:
            raise ValidationError(_("Config key already exists for some user(s), please check and retry")) from None

    def update(self, instance, validated_data):
        validated_data.pop("config_user", None)
        return super().update(instance, validated_data)

    @extend_schema_field(serializers.JSONField)
    def get_cache_value(self, obj):
        """生效值：整页序列化时一次批量预取（context 记忆），单对象仍逐 key 读取。"""
        page = self.get_page_instances(obj)
        cached = self.context.get("_page_user_cache_values")
        if cached is None and len(page) > 1:
            # 批量口径同逐行 UserConfig(owner).get_value(key)：个人行优先，缺席继承系统级
            cached = batch_user_config_values([(row.owner_id, row.key) for row in page])
            self.context["_page_user_cache_values"] = cached
        pair = (obj.owner_id, obj.key)
        if cached is not None and pair in cached:
            return cached[pair]
        return UserConfig(obj.owner).get_value(obj.key)
