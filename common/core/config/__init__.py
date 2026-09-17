#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : config
# author : ly_13
# date : 12/15/2023
# 系统配置的缓存失效由信号自动处理（system/signal_handler.py），个人级继承值
# 不落个人缓存（直读系统级），系统默认变更无需手动清理缓存；
# 手动兜底命令仍可用： python manage.py expire_caches config_*
"""系统配置缓存（SysConfig / UserConfig）。

- ``SysConfig = ConfigCache()``：系统级配置读取单例（属性 = 配置键）；
- ``UserConfig(user_obj)``：个人级配置（真实个人行优先、缺席继承系统级）；
- ``BaseConfCache`` 的默认值单源在 ``server/conf.py``（键未登记时回退默认值）。

本包按职责拆分（base / system_conf / user_conf），对外 API 由本文件统一再导出，
导入路径保持 ``common.core.config`` 不变。
"""

from .base import ConfigCacheBase, SystemConfigSerializer, get_render_context
from .system_conf import BaseConfCache, ConfigCache, MessagePushConfCache, SysConfig
from .user_conf import (
    UserConfig,
    UserConfigSerializer,
    UserPersonalConfigCache,
    batch_user_config,
    get_personal_config_data,
    get_personal_int_config,
)

__all__ = [
    "BaseConfCache",
    "ConfigCache",
    "ConfigCacheBase",
    "MessagePushConfCache",
    "SysConfig",
    "SystemConfigSerializer",
    "UserConfig",
    "UserConfigSerializer",
    "UserPersonalConfigCache",
    "batch_user_config",
    "get_personal_config_data",
    "get_personal_int_config",
    "get_render_context",
]
