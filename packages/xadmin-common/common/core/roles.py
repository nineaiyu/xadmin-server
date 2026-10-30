#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""用户生效角色解析（直挂角色 ∪ 部门挂角色）。

部门挂角色是既有授权能力（``DeptInfo.roles``）：用户属于某部门时，该部门绑定的
角色同样生效。权限/字段权限已按此口径处理，但脱敏、审批人、MFA、登录策略等
消费点曾各自只查 ``user.roles``，导致同一位用户在菜单可见、但治理策略上失配。

本模块是唯一解析入口，禁止业务侧再手写 ``user.roles`` 作为「用户全部角色」。
"""

from typing import Any

from django.db.models import Q

from common.utils import get_logger

logger = get_logger(__name__)


def _active_roles(manager: Any) -> list[Any]:
    """角色管理器 → 启用且未删除的角色（模型能力缺失时退化为仅启用过滤）。"""
    try:
        return list(manager.filter(is_active=True, deleted_at__isnull=True))
    except Exception:  # noqa: BLE001 非软删角色实现/查询异常：退化为仅启用过滤
        try:
            return list(manager.filter(is_active=True))
        except Exception:  # noqa: BLE001 角色不可读时按空集处理（与既有消费点容错口径一致）
            logger.warning("load user roles failed", exc_info=True)
            return []


def iter_user_roles(user: Any) -> list[Any]:
    """用户生效角色对象（去重保序）：直挂角色 + 所属启用部门的角色。"""
    if user is None:
        return []
    collected: list[Any] = []
    direct = getattr(user, "roles", None)
    if direct is not None:
        collected.extend(_active_roles(direct))
    dept = getattr(user, "dept", None)
    if dept is not None and getattr(dept, "is_active", True):
        dept_roles = getattr(dept, "roles", None)
        if dept_roles is not None:
            collected.extend(_active_roles(dept_roles))
    seen: set[Any] = set()
    result = []
    for role in collected:
        key = getattr(role, "pk", id(role))
        if key in seen:
            continue
        seen.add(key)
        result.append(role)
    return result


def user_role_pks(user: Any) -> set[Any]:
    """用户生效角色主键集合（脱敏等按角色 pk 匹配的消费点）。"""
    return {role.pk for role in iter_user_roles(user) if getattr(role, "pk", None) is not None}


def user_role_codes(user: Any) -> set[str]:
    """用户生效角色 code 集合（按角色 code 匹配的消费点）。"""
    return {str(role.code) for role in iter_user_roles(user) if getattr(role, "code", None)}


def role_members_q(codes: Any) -> Q:
    """UserInfo 查询谓词：直挂角色或所属启用部门绑定这些角色 code 的用户。"""
    normalized = list(dict.fromkeys(str(code) for code in (codes or []) if str(code).strip()))
    if not normalized:
        return Q(pk__in=[])
    direct = Q(
        roles__is_active=True,
        roles__deleted_at__isnull=True,
        roles__code__in=normalized,
    )
    via_dept = Q(
        dept__is_active=True,
        dept__roles__is_active=True,
        dept__roles__deleted_at__isnull=True,
        dept__roles__code__in=normalized,
    )
    return direct | via_dept


__all__ = ["iter_user_roles", "role_members_q", "user_role_codes", "user_role_pks"]
