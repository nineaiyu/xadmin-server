#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""system 域对外契约出口（跨 app 只允许从这里导入）。

当前提供 typed 设置页所需的 SystemConfig 模型与写入服务，避免调用方
直接依赖 `system.models` / `system.serializers` 的内部形态。
"""

from system.models import SystemConfig
from system.services.config import save_system_config_value

__all__ = ["SystemConfig", "save_system_config_value"]
