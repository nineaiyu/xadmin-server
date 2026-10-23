#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""AI 动作注册表：按应用自动发现 + 合并（唯一白名单入口）。

约定（应用级扩展点，二开零核心文件改动）：业务 app 提供 ``ai_declarations.py``，
以 ``AI_ACTIONS`` / ``AI_READ_ACTIONS`` / ``AI_WRITE_ACTIONS`` 字典声明动作
（值为 ``ActionSpec`` 或 ``ApiActionSpec``）：

    # myapp/ai_declarations.py
    from ai.services.actions.ai_api_actions import api_action

    AI_ACTIONS = {
        "myapp.thing.list": api_action(...),
    }

发现器遍历 ``apps.get_app_configs()``，声明随 app 安装自动纳入；未安装（模块裁剪
只拦路由，不卸载 app）或未提供文件的应用自然不参与。合并规则：

- key 全局唯一，重复直接 fail-fast（跨应用声明互踩必须在启动/测试期暴露）；
- 声明字典的 key 必须与 ``spec.key`` 一致（防复制粘贴改名漏改，静默失效）；
- 结果按 key 排序，保证工具目录与 prompt 在多环境一致；
- 进程级缓存：改声明需重启（与 ``config.py::URLPATTERNS`` / ``modules.py`` 同口径）。

首次取数发生在 django.setup 之后（目录构建、巡检命令、测试），**不要在
AppConfig.ready() 里构造**——那时 app registry 与数据库都可能未就绪。
"""

from functools import lru_cache
from importlib import import_module
from typing import Any

from django.apps import apps as django_apps
from django.core.exceptions import ImproperlyConfigured

from common.utils import get_logger

logger = get_logger(__name__)

#: 应用级声明文件名（模块名，位于应用根）
DISCOVERY_MODULE = "ai_declarations"
#: 识别的声明变量（``AI_ACTIONS`` 为新约定；两个读写分桶兼容代码生成器产物）
DECLARATION_ATTRS = ("AI_ACTIONS", "AI_READ_ACTIONS", "AI_WRITE_ACTIONS")


def _declared_specs(module: Any, module_path: str) -> dict[str, Any]:
    """读取一个声明模块的全部动作（合并三个变量桶，校验值与 key 一致）。"""
    declared: dict[str, Any] = {}
    for attr in DECLARATION_ATTRS:
        bucket = getattr(module, attr, None)
        if not bucket:
            continue
        if not isinstance(bucket, dict):
            raise ImproperlyConfigured(f"{module_path}.{attr} 必须是 dict（key → ActionSpec）")
        for key, spec in bucket.items():
            spec_key = str(getattr(spec, "key", "") or "")
            if spec_key != str(key):
                raise ImproperlyConfigured(f"{module_path}.{attr} 声明 key 与规格不一致：{key} != {spec_key}")
            if key in declared:
                raise ImproperlyConfigured(f"{module_path} 内动作声明重复：{key}")
            declared[str(key)] = spec
    return declared


def _app_declarations(app_config: Any) -> dict[str, Any]:
    """读取单个应用的声明；无声明文件返回空（文件存在但内部 import 失败必须暴露）。"""
    module_path = f"{app_config.name}.{DISCOVERY_MODULE}"
    try:
        module = import_module(module_path)
    except ModuleNotFoundError as exc:
        # 只吞「没有声明文件」；文件内的依赖缺失属于声明坏了，必须冒泡
        if exc.name not in (module_path, app_config.name):
            raise
        return {}
    return _declared_specs(module, module_path)


@lru_cache(maxsize=1)
def action_specs() -> dict[str, Any]:
    """全量动作注册表（各应用声明合并；key 排序保证多环境一致）。"""
    merged: dict[str, Any] = {}
    for app_config in django_apps.get_app_configs():
        for key, spec in _app_declarations(app_config).items():
            if key in merged:
                raise ImproperlyConfigured(f"AI 动作 key 重复：{key}（应用 {app_config.name}）")
            merged[key] = spec
    logger.debug("ai action registry discovered: %s actions", len(merged))
    return dict(sorted(merged.items()))


def reset_action_specs_cache() -> None:
    """清空发现缓存（测试 / 声明热改后的显式重建入口）。"""
    action_specs.cache_clear()
