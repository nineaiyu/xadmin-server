#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""内置角色与内置标签定义 + post_migrate 幂等同步。

- 清单固定于代码（BUILTIN_ROLES / BUILTIN_TAGS），经 post_migrate 同步到库：全新库
  migrate 后即有可用数据，不依赖 load_init_json 种子流程（存量库升级同样生效）；
- 同步幂等：按业务键定位（角色 code / 标签名），字段无变化时不落库，重复 migrate
  无副作用；被误删的内置数据在下次 migrate 自动补回；
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

# name 即业务键（Tag.name unique）：内置标签不可经 API 删除（TagViewSet 删除保护），
# 颜色/备注可在标签管理页调整——同步只补缺，不覆盖管理员的合法改动。
# 注意：name/remark 必须是纯字符串而非 gettext_lazy——name 是同步的定位键，
# 惰性翻译会随语言环境漂移（migrate 时与查询时解析结果不同 → 重复建签），
# 与 loadjson 种子数据（纯文本）同口径。
BUILTIN_TAGS = [
    {"name": "重点", "color": "#F56C6C", "remark": "内置标签：标记重点用户、文件或审批单"},
    {"name": "待跟进", "color": "#E6A23C", "remark": "内置标签：待跟进事项"},
    {"name": "归档", "color": "#909399", "remark": "内置标签：已归档对象"},
]


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


def sync_builtin_tags() -> int:
    """内置标签幂等同步，返回实际变更的标签数（0 = 全部已就绪）。

    按名称三态处理：在用 → 补 builtin 标记（颜色/备注以管理员改动为准，不回写）；
    不存在（含被误删）→ 新建。标签无软删除，无需回收站恢复分支。
    """
    from system.models import Tag

    changed = 0
    for spec in BUILTIN_TAGS:
        tag = Tag.objects.filter(name=spec["name"]).first()
        if tag is None:
            Tag.objects.create(name=spec["name"], color=spec["color"], remark=spec["remark"], builtin=True)
            changed += 1
            logger.info("builtin tag created: %s", spec["name"])
            continue
        if not tag.builtin or not tag.color or not tag.remark:
            tag.builtin = True
            tag.color = tag.color or spec["color"]
            tag.remark = tag.remark or spec["remark"]
            tag.save()
            changed += 1
            logger.info("builtin tag synced: %s", spec["name"])
    return changed
