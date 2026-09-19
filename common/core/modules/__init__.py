#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : modules
# author : ly_13
# date : 2026/09/17
"""功能模块注册表（可裁剪架构）。

本文件是「哪些功能存在、哪些默认开启」的唯一事实源（单一数据源）：

- 每个模块声明：等级 / 依赖 / 菜单子树 / 请求路由前缀 / 补充权限路径；
- 装配开关来自 config.yml 的 ``MODULE_PRESET`` / ``MODULE_ENABLE`` / ``MODULE_DISABLE``；
- 所有裁剪动作都从声明派生，不允许在别处再写一份模块清单。

生效范围（软裁剪）：
    1. 请求路由：命中禁用模块前缀的 HTTP 请求直接 404（``ModuleGateMiddleware``）；
    2. WebSocket 通道：命中禁用模块 ``ws_routes`` 声明的 WS 连接在准入层拒绝
       （``ModuleTrimWebsocketMiddleware``，close 4404）；
    3. 菜单与权限点：禁用模块的菜单子树与权限码从用户路由/鉴权结果中隐藏；
    4. 周期任务：禁用模块声明的周期任务不再注册，历史注册条目一并清理。

裁剪语义（红线）：
    1. 关闭模块只隐藏与拦截，**不删除任何业务数据**；重新开启即恢复；
    2. ``core`` 等级模块不可关闭（装配、鉴权与菜单结构依赖它们）；
    3. 依赖未满足时启动期 fail-fast，不做隐式连带禁用；
    4. 默认 ``preset=full``（全部开启），与改造前行为零差异。

已知边界（P1）：前端构建产物仍包含全部页面（按需构建裁剪见后续批次）。
边界与后续计划见 ``docs/adr/ADR-045-modular-trimmable-architecture.md``。

本包按职责拆分（specs / registry / gate / seeding），对外 API 由本文件统一再导出，
导入路径保持 ``common.core.modules`` 不变。
"""

from .gate import (
    ModuleTrimWebsocketMiddleware,
    compute_hidden_menu_pks,
    disabled_permission_prefixes,
    disabled_route_patterns,
    disabled_ws_patterns,
    filter_menu_queryset,
    invalidate_trimmed_caches,
    is_ws_path_trimmed,
    permission_prefixes_of,
)
from .override import (
    ModuleOverrideData,
    clear_override,
    load_override,
    save_override,
)
from .registry import (
    _MODULE_INDEX as _MODULE_INDEX,  # noqa: PLC0414 显式再导出（测试按私有名导入）
)
from .registry import (
    MODULES,
    all_module_specs,
    config_snippet,
    deployment_config,
    desired_modules,
    disabled_module_ids,
    discovered_modules,
    enabled_module_ids,
    is_module_enabled,
    module_diff,
    module_index,
    module_signature,
    modules_report,
    override_active,
    preset_module_ids,
    preview_modules,
    reset_module_state,
    resolve_modules,
    validate_deployment_config,
)
from .seeding import ModuleSeedFilter
from .specs import (
    CORE,
    DEFAULT_PRESET,
    MENU_TYPE_DIRECTORY,
    MENU_TYPE_PERMISSION,
    OPTIONAL,
    PRESETS,
    STANDARD,
    ModuleResolution,
    ModuleSpec,
)

__all__ = [
    "CORE",
    "DEFAULT_PRESET",
    "MENU_TYPE_DIRECTORY",
    "MENU_TYPE_PERMISSION",
    "MODULES",
    "OPTIONAL",
    "PRESETS",
    "STANDARD",
    "ModuleOverrideData",
    "ModuleResolution",
    "ModuleSeedFilter",
    "ModuleSpec",
    "ModuleTrimWebsocketMiddleware",
    "all_module_specs",
    "clear_override",
    "compute_hidden_menu_pks",
    "config_snippet",
    "deployment_config",
    "desired_modules",
    "disabled_module_ids",
    "disabled_permission_prefixes",
    "disabled_route_patterns",
    "disabled_ws_patterns",
    "discovered_modules",
    "enabled_module_ids",
    "filter_menu_queryset",
    "invalidate_trimmed_caches",
    "is_module_enabled",
    "is_ws_path_trimmed",
    "load_override",
    "module_diff",
    "module_index",
    "module_signature",
    "modules_report",
    "override_active",
    "permission_prefixes_of",
    "preset_module_ids",
    "preview_modules",
    "reset_module_state",
    "resolve_modules",
    "save_override",
    "validate_deployment_config",
]
