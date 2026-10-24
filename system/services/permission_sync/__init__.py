#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""菜单权限点同步内核（扫描 → 规划 → 应用 → 报告）。

设计见 docs/architecture/菜单权限与字段同步补全方案-2026.09.md。核心口径与菜单页
「批量生成权限」同源（复用 system.utils.platform.menu.get_view_permissions），保证：

1. 补齐的权限点 code / 描述 / 模型绑定与 UI 生成结果一致，不会产生重复项；
2. 幂等：已覆盖（精确 `path$` 或运行时的正则回退命中）的端点不重复创建；
3. 校正绑定：CRUD 动作补绑定（角色页字段权限可配置）；导入导出链保持空绑定
   （字段权限按运行时规则回退到 list/create 菜单）。

本包不打印、不交互，供 management 命令与测试复用。按职责拆分
（constants / types / scan / apply / audit / seed），对外 API 由本文件统一再导出，
对外导入路径为 ``system.services.permission_sync``（服务型模块，随服务层下沉归位）。
"""

from .apply import (
    apply_binding_fixes,
    apply_plans,
    complete_field_permissions,
    fill_field_permissions,
    grant_to_roles,
    model_field_nodes,
    plan_binding_fixes,
    snapshot_role_field_names,
)
from .audit import (
    audit_custom_permissions,
    audit_field_permissions,
    audit_permission_menus,
    audit_wide_manager_grants,
)
from .constants import CRUD_BIND_ACTIONS, DEAD_ENDPOINT_PREFIXES, IMPORT_EXPORT_ACTIONS
from .declarations import (
    AUDIT_KNOWN_DUPLICATES,
    AUDIT_SKIP_PREFIXES,
    PARENT_MENU_MAP,
    PERMISSION_EXEMPT_VIEWS,
    RUNTIME_PERMISSION_PREFIXES,
    SHARED_METHOD_PATHS,
    SKIP_ROUTE_PREFIXES,
)
from .scan import (
    build_plans,
    build_route_index,
    ensure_urlconf_loaded,
    find_covering,
    load_permission_menus,
    path_whitelisted,
    permission_face,
    refresh_titles,
    requires_permission,
    resolve_view_context,
    sample_path,
    scan_gaps,
    scan_permission_gaps,
    unclassified_views,
    url_to_sample,
)
from .seed import detect_indent, dump_entries, merge_seed_file, seed_entry_pks
from .types import PERMISSION_MENU_RANK_BASE, BindingFix, PlanItem, RouteInfo

__all__ = [
    "AUDIT_KNOWN_DUPLICATES",
    "AUDIT_SKIP_PREFIXES",
    "CRUD_BIND_ACTIONS",
    "DEAD_ENDPOINT_PREFIXES",
    "IMPORT_EXPORT_ACTIONS",
    "PARENT_MENU_MAP",
    "PERMISSION_EXEMPT_VIEWS",
    "PERMISSION_MENU_RANK_BASE",
    "RUNTIME_PERMISSION_PREFIXES",
    "SHARED_METHOD_PATHS",
    "SKIP_ROUTE_PREFIXES",
    "BindingFix",
    "PlanItem",
    "RouteInfo",
    "apply_binding_fixes",
    "apply_plans",
    "audit_custom_permissions",
    "audit_field_permissions",
    "audit_permission_menus",
    "audit_wide_manager_grants",
    "build_plans",
    "build_route_index",
    "permission_face",
    "unclassified_views",
    "complete_field_permissions",
    "detect_indent",
    "dump_entries",
    "ensure_urlconf_loaded",
    "fill_field_permissions",
    "find_covering",
    "grant_to_roles",
    "load_permission_menus",
    "merge_seed_file",
    "model_field_nodes",
    "path_whitelisted",
    "plan_binding_fixes",
    "refresh_titles",
    "requires_permission",
    "resolve_view_context",
    "sample_path",
    "scan_gaps",
    "snapshot_role_field_names",
    "scan_permission_gaps",
    "seed_entry_pks",
    "url_to_sample",
]
