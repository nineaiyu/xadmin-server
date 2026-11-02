#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""种子装配的演示数据与审计用户守卫（拆出以满足文件行数门禁）。

- 生产未启用 demo 时停用平台层演示角色 / 部门 / 数据规则；
- 空库装载时把指向不存在用户的 ``creator`` / ``modifier`` 置空。
"""

from typing import Any

from django.apps import apps
from django.conf import settings
from django.db import DEFAULT_DB_ALIAS

from .types import NOTE_PRUNE, SeedNote

#: 平台层种子中的演示数据：生产未启用 demo 时停用（保留行以便开发/演示环境复用）。
DEMO_ROLE_CODES = frozenset({"demo", "test"})
DEMO_DEPT_CODES = frozenset({"demo", "test"})
DEMO_PERMISSION_NAMES = frozenset(
    {"演示模式菜单", "测试部门只读权限", "演示部门用户范围", "演示写入数据范围", "演示只读数据范围"}
)


def deactivate_demo_rows(rows_by_model: dict[str, Any], *, demo_installed: bool) -> tuple[dict[str, Any], list[Any]]:
    """生产未启用 demo 时停用演示角色 / 部门 / 数据规则。

    这些行走平台层种子，不受 ``seed_demo_*`` 的命令注册约束；直接删除会破坏
    授权与字段权限种子的外键，故保留行但置 ``is_active=False``：不可分配、
    不参与数据权限判定，管理员也不会误绑。
    """

    if demo_installed:
        return rows_by_model, []
    targets = {
        "identity.userrole": ("code", DEMO_ROLE_CODES),
        "identity.deptinfo": ("code", DEMO_DEPT_CODES),
        "system.datapermission": ("name", DEMO_PERMISSION_NAMES),
    }
    disabled = 0
    for label, (field, values) in targets.items():
        for row in rows_by_model.get(label) or []:
            fields = row.get("fields") or {}
            if str(fields.get(field)) in values and fields.get("is_active") is not False:
                fields["is_active"] = False
                disabled += 1
    if not disabled:
        return rows_by_model, []
    return rows_by_model, [SeedNote(f"生产环境停用演示角色/部门/数据规则 {disabled} 条（demo 未启用）", NOTE_PRUNE)]


def detach_missing_audit_users(
    rows_by_model: dict[str, Any], *, using: Any = DEFAULT_DB_ALIAS
) -> tuple[dict[str, Any], list[Any]]:
    """把指向不存在用户的 ``creator`` / ``modifier`` 置空。

    种子用 ``creator: 1`` 记录首个超管，``loaddata`` 在空库（尚未跑 ``init_data``）
    会因外键违例整体回滚。创建人只是审计字段且可空，缺失时置空比拒绝装载更合理；
    有用户时保持原值，不改变既有种子语义。
    """

    user_model = apps.get_model(settings.AUTH_USER_MODEL.lower())
    if user_model is None:
        return rows_by_model, []
    referenced = {
        str(row["fields"][name])
        for rows in rows_by_model.values()
        for row in rows
        for name in ("creator", "modifier")
        if row["fields"].get(name) is not None
    }
    if not referenced:
        return rows_by_model, []
    existing = {
        str(pk)
        for pk in user_model._default_manager.using(using).filter(pk__in=list(referenced)).values_list("pk", flat=True)
    }
    missing = referenced - existing
    if not missing:
        return rows_by_model, []
    detached = 0
    for rows in rows_by_model.values():
        for row in rows:
            for name in ("creator", "modifier"):
                value = row["fields"].get(name)
                if value is not None and str(value) in missing:
                    row["fields"][name] = None
                    detached += 1
    return rows_by_model, [
        SeedNote(f"种子引用的创建人/修改人不存在（{len(missing)} 个），已置空 {detached} 处", NOTE_PRUNE)
    ]
