#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""OpenAPI schema 导出的稳定性处理。

drf-spectacular 会把模型字段的**运行期默认值**写进 schema：`uuid.uuid4` 生成的主键
默认值、`timezone.now` 生成的时间默认值——每次生成都不同，导致 schema/生成类型
出现无意义漂移。此类默认值不属于稳定的线上契约（服务端运行时生成），导出时统一
剔除；真正固定的字面量默认值（如 status 的 "pending"）保留。
"""

import re
from typing import Any

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")


def is_runtime_default(value: Any) -> bool:
    """是否为运行期生成、每次导出都会变化的默认值。"""
    if not isinstance(value, str):
        return False
    return bool(UUID_RE.match(value) or DATETIME_RE.match(value))


def normalize_schema(value: Any) -> Any:
    """递归剔除运行期默认值，返回可稳定 diff 的 schema。"""
    if isinstance(value, dict):
        return {
            key: normalize_schema(item)
            for key, item in value.items()
            if not (key == "default" and is_runtime_default(item))
        }
    if isinstance(value, list):
        return [normalize_schema(item) for item in value]
    return value


__all__ = ["is_runtime_default", "normalize_schema"]
