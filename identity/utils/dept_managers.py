#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""部门管理员任命装配：through 行 + 预置角色成员 + 用户级数据权限规则的统一维护。

设计：

- 任命事实源 = ``DeptInfo.managers``（through 记录任命人/时间），写口唯一 =
  部门 ViewSet 的 ``assign-managers`` 端点；序列化器对该字段只读；
- 部门管理员「能做什么」= 预置内置角色（权限点面，见 ``system/builtin.py``），
  「能看什么」= 两条预置数据权限规则（用户面 / 部门面，``value.manager.*``
  在运行期按任命关系解析）；
- 规则绑定在用户级（``UserRole`` 无 rules 字段，数据权限只挂用户/部门）；
- 解任回收：用户不再管理任何启用部门时移除预置角色与预置规则（幂等）；
  经任命获得的角色成员/规则由本模块唯一维护，手工调整不保证一致（巡检可见）。
"""

from typing import Any

from django.db import transaction

from common.utils import get_logger

logger = get_logger(__name__)

# 预置角色 code（与 system/builtin.py 的内置角色清单同源）
DEPT_MANAGER_ROLE_CODE = "DeptManager"

# 预置数据权限规则（名称即幂等键；table 为 model label_lower，编译期按模型匹配）
DEPT_MANAGER_RULE_SPECS = (
    {
        "name": "部门管理-本部门及下级成员",
        "rules": [
            {
                "table": "identity.userinfo",
                "field": "id",
                "type": "value.manager.user.ids",
                "match": "in",
                "value": "",
                "exclude": False,
            }
        ],
    },
    {
        "name": "部门管理-本部门及下级",
        "rules": [
            {
                "table": "identity.deptinfo",
                "field": "id",
                "type": "value.manager.dept.ids",
                "match": "in",
                "value": "",
                "exclude": False,
            }
        ],
    },
)


def ensure_manager_role() -> Any:
    """预置角色（内置，post_migrate 同步；此处兜底确保存在）。"""
    from identity.models import UserRole

    role = UserRole.all_objects.filter(code=DEPT_MANAGER_ROLE_CODE, deleted_at__isnull=True).first()
    if role is not None:
        return role
    from identity.builtin import sync_builtin_roles

    sync_builtin_roles()
    return UserRole.all_objects.filter(code=DEPT_MANAGER_ROLE_CODE, deleted_at__isnull=True).first()


def ensure_preset_rules() -> Any:
    """幂等维护两条预置规则（内容漂移就地校正，不重复落库）。"""
    from system.services import DataPermission

    result = []
    for spec in DEPT_MANAGER_RULE_SPECS:
        dp = DataPermission.objects.filter(name=spec["name"]).first()
        if dp is None:
            dp = DataPermission.objects.create(name=spec["name"], rules=spec["rules"], is_active=True)
            logger.info("dept manager preset rule created: %s", spec["name"])
        else:
            updates: dict[str, Any] = {}
            if dp.rules != spec["rules"]:
                updates["rules"] = spec["rules"]
            if not dp.is_active:
                updates["is_active"] = True
            if updates:
                for field, value in updates.items():
                    setattr(dp, field, value)
                dp.save(update_fields=list(updates))
                logger.info("dept manager preset rule synced: %s", spec["name"])
        result.append(dp)
    return result


def sync_manager_assembly(user: Any) -> bool:
    """按「用户是否仍管理任何启用部门」同步预置角色与预置规则（幂等）。

    返回用户当前是否持有管理职责（供调用方反馈）。
    """
    from identity.models import DeptManagerAssignment

    has_scope = DeptManagerAssignment.objects.filter(user=user, dept__is_active=True).exists()
    role = ensure_manager_role()
    rules = ensure_preset_rules()
    if has_scope:
        if role is not None:
            user.roles.add(role)
        user.rules.add(*rules)
    else:
        if role is not None:
            user.roles.remove(role)
        user.rules.remove(*rules)
    typed_value: bool = has_scope
    return typed_value


def managers_payload(dept: Any) -> list[Any]:
    """部门管理员清单（任命弹窗回显与端点响应共用的轻量载荷）。"""
    from identity.models import DeptManagerAssignment

    rows = DeptManagerAssignment.objects.filter(dept=dept).select_related("user").order_by("-created_time")
    return [
        {"pk": row.user_id, "username": row.user.username, "nickname": row.user.nickname} for row in rows if row.user_id
    ]


def assign_dept_managers(dept: Any, *, add_pks: Any, remove_pks: Any, operator: Any) -> list[Any]:
    """任命/解任部门管理员（增量、幂等）。

    新增只接受在用用户（失效/不存在的 pk 静默跳过，避免半成品状态）；
    移除按 through 行删除；两类变更后对受影响用户同步装配面。
    """
    from identity.models import DeptManagerAssignment, UserInfo

    add_users = list(UserInfo.objects.filter(pk__in=add_pks, is_active=True)) if add_pks else []
    removed_users = list(UserInfo.objects.filter(pk__in=remove_pks)) if remove_pks else []
    with transaction.atomic():
        for user in add_users:
            DeptManagerAssignment.objects.get_or_create(dept=dept, user=user, defaults={"created_by": operator})
        if remove_pks:
            DeptManagerAssignment.objects.filter(dept=dept, user_id__in=remove_pks).delete()
        for user in {user.pk: user for user in add_users + removed_users}.values():
            sync_manager_assembly(user)
    return managers_payload(dept)
