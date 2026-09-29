#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""菜单权限点同步内核：审计报告。"""

import re

from .constants import AUDIT_KNOWN_DUPLICATES, AUDIT_SKIP_PREFIXES


def audit_field_permissions():
    """报告「角色已获模型权限点、但未配置字段权限」的组合（只报告不落库）。

    字段权限是 **fail-closed 的零字段口径**：某 (角色, 菜单) 没有字段白名单（无
    FieldPermission 行，或行内字段为空）时，该角色在这些接口上拿到的字段集合为空——
    列表/详情输出空对象（不是"默认全字段"，见 ``common/core/serializers.py`` 的
    get_allow_fields）。新建角色、批量生成的新权限点最容易漏配，因此列入审计面：
    不改安全语义，只把「静默空输出」变成可见告警。

    返回 ``[(role, menu), ...]``（ORM 实例，供管理命令与只读 API 复用）。
    """
    from system.models import FieldPermission, Menu, UserRole

    model_menu_pks = set(
        Menu.objects.filter(menu_type=Menu.MenuChoices.PERMISSION, is_active=True)
        .filter(model__isnull=False)
        .values_list("pk", flat=True)
        .distinct()
    )
    if not model_menu_pks:
        return []
    # 行内字段为空的 FieldPermission 与"没有行"等价（都产出空白名单），一并计入缺口
    configured = set(FieldPermission.objects.filter(field__isnull=False).values_list("role_id", "menu_id").distinct())
    items = []
    for role in UserRole.objects.filter(is_active=True).prefetch_related("menu"):
        for menu in role.menu.all():
            if menu.pk in model_menu_pks and (role.pk, menu.pk) not in configured:
                items.append((role, menu))
    return items


def audit_permission_menus(routes, perms):
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
