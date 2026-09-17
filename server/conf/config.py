#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""配置对象：默认值合并 + 类型转换 + 取值回退链。"""

import json
import logging
import os
from importlib import import_module

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger("xadmin.conf")


def import_string(dotted_path):
    try:
        module_path, class_name = dotted_path.rsplit(".", 1)
    except ValueError as err:
        raise ImportError(f"{dotted_path} doesn't look like a module path") from err

    module = import_module(module_path)

    try:
        return getattr(module, class_name)
    except AttributeError as err:
        raise ImportError(f'Module "{module_path}" does not define a "{class_name}" attribute/class') from err


class DoesNotExist(Exception):
    pass


from .defaults import BASE_CONFIG, LIBS_CONFIG
from .settings_defaults import SETTINGS_CONFIG


class Config(dict):
    base = BASE_CONFIG
    libs = LIBS_CONFIG
    settings = SETTINGS_CONFIG

    defaults = {
        "API_LOG_ENABLE": True,
        # 忽略日志记录, 支持model 或者 request_path, 不支持正则
        "API_LOG_IGNORE": {
            "system.OperationLog": ["GET"],
            "/api/common/api/health": ["GET"],
        },
        "API_LOG_METHODS": ["POST", "DELETE", "PUT", "PATCH"],
        "API_MODEL_MAP": {
            "/api/system/refresh": "Token刷新",
            "/api/flower": "定时任务",
        },
    }
    defaults.update(base)
    defaults.update(libs)
    defaults.update(settings)
    old_config_map = {}

    def __init__(self, *args):
        super().__init__(*args)

    def convert_type(self, k, v):
        default_value = self.defaults.get(k)
        if default_value is None:
            return v
        tp = type(default_value)
        # 对bool特殊处理
        if tp is bool and isinstance(v, str):
            if v.lower() in ("true", "1"):
                return True
            else:
                return False
        if tp in [list, dict] and isinstance(v, str):
            try:
                v = json.loads(v)
                return v
            except json.JSONDecodeError:
                return v

        try:
            if tp in [list, dict]:
                v = json.loads(v)
            else:
                v = tp(v)
        except Exception:
            pass
        return v

    def __repr__(self):
        return f"<{self.__class__.__name__} {dict.__repr__(self)}>"

    def get_from_config(self, item):
        try:
            value = super().__getitem__(item)
        except KeyError:
            value = None
        return value

    def get_from_env(self, item):
        value = os.environ.get(item, None)
        if value is not None:
            value = self.convert_type(item, value)
        return value

    def get(self, item, default=None):
        # 再从配置文件中获取
        value = self.get_from_config(item)
        if value is None:
            value = self.get_from_env(item)

        # 因为要递归，所以优先从上次返回的递归中获取
        if default is None:
            default = self.defaults.get(item)
        if value is None and item in self.old_config_map:
            return self.get(self.old_config_map[item], default)
        if value is None:
            value = default
        return value

    def __getitem__(self, item):
        return self.get(item)

    def __getattr__(self, item):
        return self.get(item)
