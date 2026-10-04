# -*- coding: utf-8 -*-
#
import os

from .conf import ConfigManager

__all__ = ["PROJECT_DIR", "VERSION", "CONFIG", "LOG_DIR", "TMP_DIR", "CELERY_LOG_DIR"]

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(PROJECT_DIR, "data", "logs")
TMP_DIR = os.path.join(PROJECT_DIR, "tmp")
CELERY_LOG_DIR = os.path.join(LOG_DIR, "task")
VERSION = "4.2.5"
CONFIG = ConfigManager.load_user_config()

# 装配产物（静态配置 / 版本号）依赖注入给 common 框架层——common 不再
# 反向 import server（门禁：scripts/check_cross_app_imports.py），统一经
# common.injection.get_server_config / get_server_version 读取。
# 本文件位于 settings 导入链最前端，此处注入先于一切 common 配置消费。
from common.injection import register_server_injection  # noqa: E402

register_server_injection(config=CONFIG, version=VERSION)
