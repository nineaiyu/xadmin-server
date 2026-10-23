#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SystemConfig 写入服务：typed 设置页复用既有校验 / 加密 / 缓存失效。"""

from typing import Any

from common.core.config import SysConfig, UserConfig
from common.utils import get_logger
from system.models import SystemConfig

logger = get_logger(__name__)


def save_system_config_value(
    key: str,
    value: Any,
    *,
    user: Any = None,
    request: Any = None,
) -> SystemConfig:
    """按 SystemConfig 的既有序列化器写入一个配置键。

    - 类型校验 / 敏感字段加密 / 缓存失效与系统配置页同源；
    - 行不存在时创建并记录 creator；存在时保留原 creator；
    - 写后强制 is_active=True（typed 设置页保存即启用，避免「保存了但没生效」）。
    """
    from system.serializers.config import SystemConfigSerializer

    row = SystemConfig.objects.filter(key=key).first()
    serializer = SystemConfigSerializer(
        row,
        data={"key": key, "value": value},
        partial=row is not None,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)
    obj: SystemConfig = serializer.save(**({"creator": user} if row is None else {}))
    if not obj.is_active:
        obj.is_active = True
        obj.save(update_fields=["is_active", "updated_time"])
    SysConfig.invalid_config_cache(key=key)
    UserConfig("*").invalid_config_cache(key=key)
    logger.info("system config saved. key:%s user:%s", key, getattr(user, "pk", None))
    return obj
