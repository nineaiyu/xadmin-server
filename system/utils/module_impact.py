# -*- coding: utf-8 -*-
"""功能裁剪预演：在真实库上计算某个模块组合的影响面（只读）。

「切换预设前先评估影响」与 ``manage.py modules --impact`` 共用。原则：

- **只读**：不修改配置、不写库、不重启，仅按给定 resolution 复算一遍裁剪口径；
- **同口径**：隐藏范围与运行期、种子导入共用 ``compute_hidden_menu_pks``，
  不会出现「预演说隐藏 A、实际隐藏 B」；
- **授权数据不动**：裁剪是运行期过滤，角色绑定无需调整，开回即恢复。
"""

from __future__ import annotations

import collections

from common.core.modules import (
    compute_hidden_menu_pks,
    module_index,
    permission_prefixes_of,
    resolve_modules,
)

# 相关表的表名特征（用于「数据保留」一节的体量参考；裁剪不删除任何行）
TABLE_HINTS = (
    "chat",
    "ai",
    "knowledge",
    "dataset",
    "dashboard",
    "screen",
    "report",
    "form",
    "approval",
    "leave",
    "webhook",
    "api_app",
    "application",
    "oauth",
    "global_search",
    "search",
    "scim",
    "ldap",
)


def specs_of(resolution) -> tuple:
    """该 resolution 下被停用的模块声明（按 id 排序，保证输出稳定）。"""

    index = module_index()
    return tuple(index[module_id] for module_id in sorted(resolution.disabled))


def menu_rows() -> list:
    """菜单表的最小字段快照（与 ``compute_hidden_menu_pks`` 的入参口径一致）。"""

    from system.models import Menu

    return list(Menu.objects.values_list("pk", "parent_id", "menu_type", "name", "path"))


def _counts_by_type(rows, pks) -> collections.Counter:
    return collections.Counter(menu_type for pk, _parent, menu_type, _name, _path in rows if pk in pks)


def _display_name(role) -> str:
    for field in ("name", "label", "code"):
        value = getattr(role, field, None)
        if value:
            return str(value)
    return f"#{role.pk}"


def _related_tables() -> list:
    """相关表体量（近似；非 postgres 或无权读取时为空白）。"""

    from django.db import connection

    if connection.vendor != "postgresql":
        return []
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "select relname, n_live_tup from pg_stat_user_tables where n_live_tup > 0 order by n_live_tup desc"
            )
            stats = [(name, rows) for name, rows in cursor.fetchall()]
    except Exception:  # noqa: BLE001 统计失败不影响预演主流程
        return []
    related = [(name, rows) for name, rows in stats if any(hint in name for hint in TABLE_HINTS)]
    return [{"table": name, "rows": rows} for name, rows in related[:30]]


def module_impact(resolution=None) -> dict:
    """计算模块组合在当前库上的影响面。

    :param resolution: 待预演的模块解析结果（默认当前生效配置）
    :return: 结构化影响面（菜单/权限点计数、逐模块明细、角色影响、相关表体量）
    """

    from django.contrib.auth import get_user_model

    from system.models import Menu, UserRole

    resolution = resolution or resolve_modules()
    rows = menu_rows()
    types = Menu.MenuChoices
    total = _counts_by_type(rows, {pk for pk, *_rest in rows})

    modules = []
    for spec in specs_of(resolution):
        # 逐模块口径：只算该模块自身波及的范围（目录清空判定只看本模块的子节点，
        # 因此逐模块求和可能小于下面的并集总数，属预期）
        own = compute_hidden_menu_pks(rows, names=spec.menus, prefixes=permission_prefixes_of((spec,)))
        counts = _counts_by_type(rows, own)
        modules.append(
            {
                "id": spec.id,
                "label": spec.label,
                "level": spec.level,
                "directories": counts[types.DIRECTORY],
                "pages": counts[types.MENU],
                "permissions": counts[types.PERMISSION],
                "routes": len(spec.routes),
            }
        )

    names = {name for spec in specs_of(resolution) for name in spec.menus}
    prefixes = permission_prefixes_of(specs_of(resolution))
    hidden = compute_hidden_menu_pks(rows, names=names, prefixes=prefixes) if (names or prefixes) else frozenset()
    hidden_counts = _counts_by_type(rows, hidden)

    user_model = get_user_model()
    roles = []
    for role in UserRole.objects.all().order_by("pk"):
        bound = set(role.menu.values_list("pk", flat=True))
        roles.append(
            {
                "name": _display_name(role),
                "bound": len(bound),
                "hidden": len(bound & hidden),
                "users": user_model.objects.filter(roles=role, is_active=True).count(),
            }
        )

    return {
        "preset": resolution.preset,
        "enabled": len(resolution.enabled),
        "disabled": sorted(resolution.disabled),
        "total": {
            "directories": total[types.DIRECTORY],
            "pages": total[types.MENU],
            "permissions": total[types.PERMISSION],
        },
        "hidden": {
            "directories": hidden_counts[types.DIRECTORY],
            "pages": hidden_counts[types.MENU],
            "permissions": hidden_counts[types.PERMISSION],
        },
        "modules": modules,
        "roles": roles,
        "tables": _related_tables(),
    }
