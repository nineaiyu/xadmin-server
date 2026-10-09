#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""功能模块声明（二开扩展点）：app 包内提供 ``MODULES`` 元组即随安装自动进入模块清单。

发现路径：``common/core/modules/registry.py::discovered_modules`` 遍历已安装 app 的
``{app}.modules``，读取模块级 ``MODULES``。声明随包分发、无需改宿主源码；导入失败
只告警跳过（扩展点故障不拖垮内核启动）。

声明进入清单后，本模块与内置模块享受同一套能力：

- **发行预设**：``core`` / ``standard`` / ``full`` 按 ``level`` 决定默认开关（本插件为
  ``optional``：仅 ``full`` 预设默认开启）；
- **六层裁剪**：请求路由 404、WS 通道拒绝（4404）、菜单/权限点隐藏、周期任务跳过、
  种子导入过滤、缓存失效；
- **依赖校验**：``depends`` 未满足时启动期 fail-fast。
"""

from common.core.modules import OPTIONAL, ModuleSpec

MODULES = (
    ModuleSpec(
        "demo_plugin",
        "示例二开插件",
        OPTIONAL,
        menus=("DemoPlugin",),  # 菜单根 name：含其全部后代菜单与权限点
        routes=(r"^/api/plugin-demo/",),  # 未写时按 config.py::URLPATTERNS 推导
        ws_routes=(r"^/ws/plugin-demo/",),  # WS 通道准入拦截（4404）
        note="示例二开插件：契约注入 / 独立路由 / WS 通道 / 周期任务（模块裁剪六层全覆盖）",
    ),
)
