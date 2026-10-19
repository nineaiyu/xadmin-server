#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""AI 平台对外契约门面（``ai.services``）。

框架层（common）经此消费 AI 能力：跨 app 门禁规定 common 只允许
``from <app>.services import ...`` 模块级引用业务 app。

动作注册表由各应用 ``ai_declarations.py`` 声明、``ai.utils.ai_registry`` 自动
发现合并；本门面只负责把合并结果交给框架层（OpenAPI 元数据派生等消费面），
不持有第二份能力清单。
"""

from typing import Any

__all__ = ["api_action_specs"]


def api_action_specs() -> dict[str, Any]:
    """AI 动作声明注册表（MCP / 助手页 / function calling / OpenAPI 元数据同源）。"""
    from ai.utils.ai_registry import action_specs

    return action_specs()


def __getattr__(name: Any) -> Any:
    # 惰性再导出：注册表常量供声明式消费方按需获取（首次访问触发发现）
    if name == "API_ACTION_SPECS":
        return api_action_specs()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
