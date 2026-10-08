#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""server 层装配产物注入点：消除 common → server 反向依赖。

common 是被所有人依赖的框架层，此前直接 ``from server.const import CONFIG``
读静态配置（config.yml / 环境变量的装配结果），构成 14 处反向依赖中最大的一股。
本模块把方向反转成依赖注入：server/const.py 在装配出 CONFIG / VERSION 后调用
``register_server_injection`` 登记，common 侧一律经 ``get_server_config`` /
``get_server_version`` 读取，不再感知装配方是谁。

登记发生在 settings 导入链最前端（server.settings → base/apps/setting →
..const），先于一切 common 配置消费（conf_* 属性为请求期惰性读取，services
命令的 hands.py 在命令执行期读——均晚于注入），未登记即读属装配顺序破坏，
直接抛 ImproperlyConfigured 暴露（静默回退默认值正是它要治的病）。

本模块必须保持零重依赖：server/const.py 在 Django settings 完成前 import 它，
不得引入任何访问 django.conf.settings 的模块。
"""

from typing import Any

from django.core.exceptions import ImproperlyConfigured

_config: Any = None
_version: str | None = None


def register_server_injection(config: Any, version: str) -> None:
    """登记 server 装配产物（server/const.py 末尾调用；重复 import 幂等覆盖）。"""
    global _config, _version
    _config = config
    _version = version


def get_server_config() -> Any:
    """读取静态配置实例 CONFIG（config.yml / 环境变量的值或代码默认值）。"""
    if _config is None:
        raise ImproperlyConfigured(
            "server CONFIG 尚未注入 common（common.injection 未登记）。"
            "确认经 Django settings（server.settings）启动，server/const.py 的注入已执行。"
        )
    return _config


def get_server_version() -> str:
    """读取平台版本号（单一事实源 server/const.py 的 VERSION，文档门禁按此校验）。"""
    if _version is None:
        raise ImproperlyConfigured(
            "server VERSION 尚未注入 common（common.injection 未登记）。"
            "确认经 Django settings（server.settings）启动，server/const.py 的注入已执行。"
        )
    return _version
