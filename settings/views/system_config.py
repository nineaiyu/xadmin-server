#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SystemConfig 型 typed 设置视图基类。

`BaseSettingViewSet` 面向 `Setting` 表；SCIM / 第三方登录等配置的消费方是
`SysConfig`（SystemConfig），保存到 Setting 会「保存成功但运行期读不到」。
本基类保留同样的 typed 序列化器 / search-columns 形态，把读写都收口到
SystemConfig 的既有序列化器（校验、字段级加密、缓存失效同源）。
"""

from typing import Any

from rest_framework.viewsets import GenericViewSet

from common.core.config import SysConfig
from common.core.modelset import SearchColumnsAction
from common.core.modelset.base import BaseViewSet
from common.core.response import ApiResponse
from system.contracts import SystemConfig, save_system_config_value


class SystemConfigSettingViewSet(BaseViewSet, SearchColumnsAction, GenericViewSet):
    """SystemConfig 型设置基类

    按字段名读写 SystemConfig，保留 typed 序列化器 / search-columns 形态，
    写入复用 SystemConfig 的既有序列化器（校验、字段级加密、缓存失效同源）。
    """

    serializer_class: Any = None
    config_keys: tuple[str, ...] = ()
    queryset = SystemConfig.objects.none()

    def get_serializer_class(self) -> Any:
        return self.serializer_class

    def get_values(self) -> dict[str, Any]:
        return {key: getattr(SysConfig, key, None) for key in self.config_keys}

    def get_object(self) -> dict[str, Any]:
        return self.get_values()

    def retrieve(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取{cls}的详情"""
        values = dict(self.get_values())
        values.update(self.get_readonly_values(values))
        serializer = self.get_serializer(values)
        return ApiResponse(data=serializer.data)

    def partial_update(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """更新{cls}配置"""
        current = self.get_values()
        current.update(self.get_readonly_values(current))
        context = self.get_serializer_context()
        context["current_values"] = current
        serializer = self.get_serializer(data=request.data, partial=True, context=context)
        serializer.is_valid(raise_exception=True)
        for key, value in serializer.validated_data.items():
            if key not in self.config_keys:
                continue
            if getattr(serializer.fields.get(key), "write_only", False) and value in ("", None):
                # 密文留空 = 保持已存值（与 Setting 体系同口径）
                continue
            self.save_key(key, value, request)
        return self.retrieve(request)

    def get_readonly_values(self, values: dict[str, Any]) -> dict[str, Any]:
        """子类可补充只读派生字段（如「令牌是否已配置」）。"""
        return {}

    def save_key(self, key: str, value: Any, request: Any) -> None:
        """用 SystemConfig 的既有写入服务：类型校验 / 加密 / 缓存失效同源。"""
        save_system_config_value(key, value, user=getattr(request, "user", None), request=request)
