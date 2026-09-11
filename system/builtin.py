#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""内置角色定义与 post_migrate 幂等同步（借鉴 jumpserver builtin.BuiltinRole）。

- 清单固定于代码（BUILTIN_ROLES），角色经 post_migrate 同步到库：全新库 migrate
  后即有可用角色，不依赖 load_init_json 种子流程（存量库升级同样生效）；
- 同步幂等：按 code 定位（含回收站中的软删角色——恢复而非重建），字段无变化时
  不落库，重复 migrate 无副作用；
- SystemAdmin 自动挂全部活跃菜单（角色初始无成员，授权仍由管理员手动分配）；
  SystemAuditor 不预挂菜单（审计菜单由管理员按需配置，登记边界）。
"""

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)

# code 不可变更：代码与治理配置（如 APPROVAL_APPROVER_ROLES）按 code 引用
BUILTIN_ROLES = [
    {
        "code": "SystemAdmin",
        "name": _("System Administrator"),
        "description": _("Builtin role: grants all active menus automatically on sync"),
        "grant_all_menus": True,
    },
    {
        "code": "SystemAuditor",
        "name": _("Auditor"),
        "description": _("Builtin role: assign audit menus manually as needed"),
        "grant_all_menus": False,
    },
]

BUILTIN_ROLE_CODES = frozenset(spec["code"] for spec in BUILTIN_ROLES)


def sync_builtin_roles() -> int:
    """内置角色幂等同步，返回实际变更的角色数（0 = 全部已就绪）。

    每条内置角色按 code 三态处理：在用 → 补标记/改名；回收站 → 恢复并补标记；
    不存在 → 新建。字段无变化时跳过落库（避免每次 migrate 触发 post_save 信号噪声）。
    """
    from system.models import Menu, UserRole

    changed = 0
    for spec in BUILTIN_ROLES:
        role = UserRole.all_objects.filter(code=spec["code"], deleted_at__isnull=True).first()
        if role is None:
            role = UserRole.all_objects.filter(code=spec["code"], deleted_at__isnull=False).first()
            if role is not None:
                # 回收站中的同名角色：恢复（同步语义优先，内置角色不应停留在已删除态）
                role.deleted_at = None
                role.builtin = True
                role.is_active = True
                role.name = spec["name"]
                role.description = spec["description"]
                role.save()
                changed += 1
                logger.info("builtin role restored from recycle bin: %s", spec["code"])
            else:
                role = UserRole.objects.create(
                    name=spec["name"],
                    code=spec["code"],
                    builtin=True,
                    description=spec["description"],
                )
                changed += 1
                logger.info("builtin role created: %s", spec["code"])

        updates = {}
        if role.name != spec["name"]:
            updates["name"] = spec["name"]
        if role.description != spec["description"]:
            updates["description"] = spec["description"]
        if not role.builtin:
            updates["builtin"] = True
        if not role.is_active:
            updates["is_active"] = True
        if role.deleted_at is not None:
            updates["deleted_at"] = None
        if updates:
            for field, value in updates.items():
                setattr(role, field, value)
            role.save()
            changed += 1
            logger.info("builtin role synced (%s): %s", ",".join(updates), spec["code"])

        if spec.get("grant_all_menus"):
            active_menu_ids = list(Menu.objects.filter(is_active=True).values_list("pk", flat=True))
            if set(role.menu.values_list("pk", flat=True)) != set(active_menu_ids):
                with transaction.atomic():
                    role.menu.set(active_menu_ids)
    return changed
