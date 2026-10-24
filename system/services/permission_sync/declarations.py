#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""菜单权限点同步内核：app 侧声明的聚合视图。

各 app 的例外清单（扫描跳过 / 审计豁免 / 父菜单归属 / 运行期自建权限点 /
custom 权限面豁免）已迁至 ``{app}/declarations.py``（契约见
``common.core.declarations``），本模块在导入期发现并合并为既有常量名——
消费面（scan / audit / apply / seed / 命令）保持 ``from .declarations import X``
或包级 ``permission_sync.X`` 不变。

框架级约定（不随 app 走的动作口径）仍在 ``constants.py``。
"""

from common.core.declarations import discover_app_declarations, merge_app_declarations

_MERGED = merge_app_declarations(discover_app_declarations())

# 不参与扫描的路由前缀（声明方：demo 等不投入权限点扫描的 app）
SKIP_ROUTE_PREFIXES = _MERGED.skip_route_prefixes
# 无同源权限点时按路由前缀指定父菜单（Menu.name，必须为页面菜单）
PARENT_MENU_MAP = _MERGED.parent_menu_map
# 审计豁免前缀（有权限点但不在可扫描路由面内，运行期经权限链正则回退命中）
AUDIT_SKIP_PREFIXES = _MERGED.audit_skip_prefixes
# 已知「同端点双权限码」重复点：各自服务不同 UI 入口/动作（非脏数据，不报告、不合并）
AUDIT_KNOWN_DUPLICATES = frozenset(_MERGED.audit_known_duplicates)
# 已知「单权限码覆盖同端点多方法」：键 = 路由正则原文（含 `$`，与权限点 path 同口径）
SHARED_METHOD_PATHS = _MERGED.shared_method_paths
# 运行时模块自建权限点的 pk 前缀：绑定与字段白名单由模块自身的种子命令维护，
# 权限点扫描不校正、种子回写不收录（其菜单/结构不进 loadjson）
RUNTIME_PERMISSION_PREFIXES = _MERGED.runtime_permission_pk_prefixes
# custom 权限面（自管权限类，不走菜单权限链）的显式豁免：视图路径 → 自管原因。
# scan.unclassified_views 逐条比对——新增 custom 视图必须在所属 app 登记，
# 否则 sync_menu_permissions 直接失败（未登记 = 非超管恒 403 无告警）。
PERMISSION_EXEMPT_VIEWS = _MERGED.permission_exempt_views

__all__ = [
    "AUDIT_KNOWN_DUPLICATES",
    "AUDIT_SKIP_PREFIXES",
    "PARENT_MENU_MAP",
    "PERMISSION_EXEMPT_VIEWS",
    "RUNTIME_PERMISSION_PREFIXES",
    "SHARED_METHOD_PATHS",
    "SKIP_ROUTE_PREFIXES",
]
