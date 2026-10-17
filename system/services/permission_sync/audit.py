#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""菜单权限点同步内核：审计报告。"""

import re
from typing import Any

from .constants import AUDIT_KNOWN_DUPLICATES, AUDIT_SKIP_PREFIXES


def audit_field_permissions(roles: Any = None) -> Any:
    """报告「角色已获模型权限点、但白名单未覆盖该权限点模型」的组合（只报告不落库）。

    ``roles`` 限定角色范围（默认全部启用角色），供单角色补齐场景复用同一判定口径。

    字段权限是 **fail-closed 的零字段口径**：某 (角色, 菜单) 的字段白名单里没有
    该菜单绑定模型（``Menu.model``）的字段时，该角色在这些接口上拿到的字段集合为空——
    列表/详情输出空对象（不是"默认全字段"，见 ``packages/xadmin-common/common/core/serializers.py`` 的
    get_allow_fields）。两类组合视为缺口：
    ① 无 FieldPermission 行，或行内字段为空；
    ② 白名单非空但与绑定模型**完全脱节**（如数据源模型迁移后残留旧模型字段）——
       运行时按「模型标签 + 字段名」匹配，脱节白名单等价于空。
    新建角色、批量生成的新权限点、视图模型变更最容易漏配，因此列入审计面：
    不改安全语义，只把「静默空输出」变成可见告警。

    返回 ``[(role, menu), ...]``（ORM 实例，供管理命令与只读 API 复用）。
    """
    from django.db.models import Prefetch

    from identity.services import UserRole
    from system.models import FieldPermission, Menu, ModelLabelField

    # 绑定模型按「角色字段树根节点」一次性预取：逐菜单读 menu.model.all() 会把每个
    # 权限点变成一条查询（数百条 N+1），种子收尾的全量审计每轮都跑这份读取，是大批量
    # 小往返的主要来源。预取成属性后判定口径不变（仍只取 field_type=ROLE）。
    role_bindings = Prefetch(
        "model",
        queryset=ModelLabelField.objects.filter(field_type=ModelLabelField.FieldChoices.ROLE),
        to_attr="role_bindings",
    )
    model_menus = list(
        Menu.objects.filter(menu_type=Menu.MenuChoices.PERMISSION, is_active=True)
        .filter(model__isnull=False)
        .distinct()
        .prefetch_related(role_bindings)
    )
    if not model_menus:
        return []
    # 菜单 → 绑定模型的角色字段树根节点（白名单字段的 parent 应落在其中）
    menu_roots: dict[Any, set[Any]] = {}
    for menu in model_menus:
        roots = {node.pk for node in menu.role_bindings}
        if roots:
            menu_roots[menu.pk] = roots
    if not menu_roots:
        return []
    # (role, menu) → 白名单字段的 parent 节点集合；行内字段为空的行等价于"没有行"
    configured: dict[Any, set[Any]] = {}
    for role_id, menu_id, parent_id in FieldPermission.objects.values_list("role_id", "menu_id", "field__parent_id"):
        configured.setdefault((role_id, menu_id), set()).add(parent_id)
    items = []
    role_queryset = UserRole.objects.filter(is_active=True).prefetch_related("menu")
    if roles is not None:
        role_queryset = role_queryset.filter(pk__in=[role.pk for role in roles])
    for role in role_queryset:
        for menu in role.menu.all():
            bound_roots = menu_roots.get(menu.pk)
            if bound_roots is None:
                continue
            parents = configured.get((role.pk, menu.pk))
            if not parents or not (parents & bound_roots):
                items.append((role, menu))
    return items


def audit_wide_manager_grants() -> Any:
    """报告「部门管理员持有宽数据权限规则」的配置（只报告不落库）。

    数据权限多授权并集取最宽（取最宽生效）：部门管理员一旦（经个人或所在部门
    祖先链绑定）持有 ``value.all`` 等不收敛规则，部门边界即失效。本检查把该
    配置风险显式暴露，不阻断不改语义——管理员可据此调整，或确认是有意为之。

    返回 ``[(user, rule_name), ...]``（部门管理员 × 命中的宽规则名）。
    """
    from common.core.data_scope import KeyChoices
    from identity.services import DeptInfo, DeptManagerAssignment, UserInfo
    from system.models import DataPermission

    manager_ids = set(DeptManagerAssignment.objects.values_list("user_id", flat=True))
    if not manager_ids:
        return []
    findings = []
    seen = set()
    for user in UserInfo.objects.filter(pk__in=manager_ids, is_active=True).select_related("dept"):
        bound = list(user.rules.filter(is_active=True))
        if user.dept_id:
            # 部门祖先链（含本部门，仅启用部门）绑定的授权与个人授权同池生效
            chain = [str(pk) for pk in DeptInfo.recursion_dept_info(user.dept_id, is_parent=True)]
            active_chain = list(DeptInfo.objects.filter(pk__in=chain, is_active=True).values_list("pk", flat=True))
            if active_chain:
                bound += list(DataPermission.objects.filter(is_active=True, deptinfo__in=active_chain).distinct())
        for dp in bound:
            if dp.pk in seen:
                continue
            seen.add(dp.pk)
            if any(isinstance(rule, dict) and rule.get("type") == KeyChoices.ALL for rule in (dp.rules or [])):
                findings.append((user, dp.name))
    return findings


def audit_permission_menus(routes: Any, perms: Any) -> Any:
    """报告：未匹配任何路由的权限点 / 重复的 (path, method)。

    匹配用「样例化为真实请求路径」的路由地址（正则原文含 `(?P<pk>...)`，
    不能直接做字符串/正则比较）。
    """
    unmatched, duplicates, exempted, known_duplicates = [], [], [], []
    seen = {}
    for perm in perms:
        key = (perm.path, (perm.method or "").upper())
        if key in seen:
            if key in AUDIT_KNOWN_DUPLICATES:
                known_duplicates.append(perm)
            else:
                duplicates.append(perm)
        else:
            seen[key] = perm

        if perm.path.startswith(AUDIT_SKIP_PREFIXES):
            exempted.append(perm)
            continue

        method = (perm.method or "").upper()
        matched = False
        for route in routes:
            if method and method not in {item.upper() for item in route.actions}:
                continue
            sample = route.sample
            if perm.path in (sample.lstrip("/"), f"{sample.lstrip('/')}$"):
                matched = True
                break
            try:
                if re.match("/" + perm.path, sample) or re.match("/" + perm.path, f"{sample}/"):
                    matched = True
                    break
            except re.error:
                continue
        if not matched:
            unmatched.append(perm)
    return unmatched, duplicates, exempted, known_duplicates
