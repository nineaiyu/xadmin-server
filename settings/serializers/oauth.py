#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第三方登录 Provider 设置序列化器（结构化编辑 + 原始 JSON 双形态）。

后端存储仍是 `SystemConfig.OAUTH_PROVIDERS` 单键；本序列化器只做形状校验，
写入复用 SystemConfig 的既有序列化器（敏感字段加密 / 缓存失效同源）。
"""

from typing import Any

from rest_framework import serializers

from identity.utils.oauth import validate_providers


class OAuthProviderSettingSerializer(serializers.Serializer):
    providers = serializers.ListField(child=serializers.DictField(), required=False)

    def validate_providers(self, value: Any) -> Any:
        return validate_providers(value)
