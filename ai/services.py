#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""AI 平台对外契约门面（``ai.services``）。

框架层（common）经此消费 AI 能力：跨 app 门禁规定 common 只允许
``from <app>.services import ...`` 模块级引用业务 app。
"""

__all__ = ["api_action_specs"]


def api_action_specs() -> dict:
    """AI 动作声明注册表（MCP / 助手页 / function calling / OpenAPI 元数据同源）。"""
    from ai.utils.ai_api_registry import API_ACTION_SPECS

    return API_ACTION_SPECS


def __getattr__(name):
    # 惰性再导出：注册表常量供声明式消费方按需获取
    if name == "API_ACTION_SPECS":
        from ai.utils.ai_api_registry import API_ACTION_SPECS as _specs

        globals()[name] = _specs
        return _specs
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
