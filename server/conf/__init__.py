#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""服务器配置（server/conf）：默认值 + 配置对象 + 管理器。

- ``defaults`` / ``settings_defaults``：config.yml 与代码默认值单源
  （守护测试校验与 loadjson/systemconfig.json 种子一致）；
- ``config``：Config 对象（类型转换与取值回退链）与 ``import_string`` / ``DoesNotExist``；
- ``manager``：ConfigManager（py/json/yaml/对象装配、load_user_config）。

本包拆分自单文件 ``server/conf.py``，对外 API 由本文件统一再导出，
导入路径保持 ``server.conf`` 不变。
"""

from .config import PROJECT_DIR, Config, DoesNotExist, import_string
from .manager import ConfigManager

__all__ = ["PROJECT_DIR", "Config", "ConfigManager", "DoesNotExist", "import_string"]
