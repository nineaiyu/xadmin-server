#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""权限可视化：用户/部门/角色三个维度的只读预览载荷。"""

from django.conf import settings

from common.core.filter import get_filter_queryset
from system.models import DataPermission, DeptInfo, FieldPermission, Menu, UserInfo, UserRole

from .constants import DEPT_PREVIEW_NOTES, PREVIEW_USER_SAMPLE_LIMIT
from .decode import decode_data_permission
from .labels import _new_label_cache
from .queries import (
    _count_menu_nodes,
    _field_groups,
    _serialize_menu_tree,
    get_trial_candidates,
    get_user_api_permissions,
    get_user_data_permissions,
    get_user_field_matrix,
    get_user_menu_queryset_for_preview,
)


def get_user_preview(user_obj: UserInfo) -> dict:
    """用户维度三层权限预览全量载荷。"""
    menu_queryset = get_user_menu_queryset_for_preview(user_obj)
    page_menus = None
    if menu_queryset:
        page_menus = menu_queryset.filter(menu_type__in=[Menu.MenuChoices.DIRECTORY, Menu.MenuChoices.MENU])
    menu_tree = _serialize_menu_tree(page_menus)
    api_permissions = get_user_api_permissions(user_obj)
    data_permissions = get_user_data_permissions(user_obj)
    field_permissions = get_user_field_matrix(user_obj)
    trial_candidates = get_trial_candidates()
    return {
        "user": {
            "pk": str(user_obj.pk),
            "username": user_obj.username,
            "nickname": user_obj.nickname,
            "is_active": user_obj.is_active,
            "is_superuser": user_obj.is_superuser,
            "dept": {"pk": str(user_obj.dept.pk), "name": user_obj.dept.name} if user_obj.dept else None,
            "roles": [
                {"pk": str(role.pk), "name": role.name, "code": role.code, "is_active": role.is_active}
                for role in user_obj.roles.all()
            ],
        },
        "menu_tree": menu_tree,
        "api_permissions": api_permissions,
        "data_permissions": data_permissions,
        "field_permissions": field_permissions,
        "field_permission_enabled": settings.PERMISSION_FIELD_ENABLED,
        "trial_candidates": trial_candidates,
        "summary": {
            "menu_count": _count_menu_nodes(menu_tree),
            "api_code_count": len(api_permissions),
            "data_permission_count": len(data_permissions["personal"])
            + sum(len(item["permissions"]) for item in data_permissions["dept_chain"]),
            "field_permission_count": len(field_permissions),
        },
    }


def get_dept_preview(dept_obj: DeptInfo, operator: UserInfo) -> dict:
    """部门维度授权预览：部门信息 + 挂载角色 / 数据权限 / 字段权限 + 成员采样。

    与用户/角色预览同源：菜单树复用 _serialize_menu_tree（自动补齐祖先），
    数据权限复用 decode_data_permission（subject="dept"：主语是「部门成员」，
    因为部门维度没有单一用户上下文），成员列表经调用者数据权限过滤（防水平越权）。
    """
    label_cache = _new_label_cache()
    roles = list(dept_obj.roles.all())
    data_permissions = [
        decode_data_permission(dp, None, label_cache, subject="dept")
        for dp in DataPermission.objects.filter(is_active=True).filter(deptinfo=dept_obj).prefetch_related("menu__meta")
    ]

    # 部门挂载角色带来的页面菜单并集（成员实际可见菜单还叠加各自的个人角色）
    menu_pks = set()
    for role in roles:
        menu_pks.update(role.menu.values_list("pk", flat=True))
    page_menus = Menu.objects.filter(
        pk__in=menu_pks, is_active=True, menu_type__in=[Menu.MenuChoices.DIRECTORY, Menu.MenuChoices.MENU]
    )
    menu_tree = _serialize_menu_tree(page_menus)

    field_permissions = []
    if roles:
        queryset = (
            FieldPermission.objects.filter(role__in=roles)
            .select_related("menu__meta", "role")
            .prefetch_related("field__parent")
        )
        for fp in queryset:
            field_permissions.append(
                {
                    "menu": {"pk": str(fp.menu.pk), "title": fp.menu.meta.title},
                    "role": {"pk": str(fp.role.pk), "name": fp.role.name},
                    "models": _field_groups(fp),
                }
            )

    users_queryset = get_filter_queryset(UserInfo.objects.filter(dept=dept_obj), operator)
    total = users_queryset.count()
    users = [
        {
            "pk": str(user.pk),
            "username": user.username,
            "nickname": user.nickname,
            "is_active": user.is_active,
        }
        for user in users_queryset[:PREVIEW_USER_SAMPLE_LIMIT]
    ]

    children = DeptInfo.objects.filter(parent=dept_obj)
    return {
        "dept": {
            "pk": str(dept_obj.pk),
            "name": dept_obj.name,
            "code": dept_obj.code,
            "is_active": dept_obj.is_active,
            "rank": dept_obj.rank,
            "parent": ({"pk": str(dept_obj.parent.pk), "name": dept_obj.parent.name} if dept_obj.parent else None),
            "leader": (
                {
                    "pk": str(dept_obj.leader.pk),
                    "username": dept_obj.leader.username,
                    "nickname": dept_obj.leader.nickname,
                }
                if dept_obj.leader
                else None
            ),
            "child_count": children.count(),
            "active_child_count": children.filter(is_active=True).count(),
        },
        "roles": [
            {"pk": str(role.pk), "name": role.name, "code": role.code, "is_active": role.is_active} for role in roles
        ],
        "menu_tree": menu_tree,
        "data_permissions": {
            "enabled": settings.PERMISSION_DATA_ENABLED,
            "has_any_grant": bool(data_permissions),
            "rules": data_permissions,
        },
        "field_permissions": field_permissions,
        "field_permission_enabled": settings.PERMISSION_FIELD_ENABLED,
        "users": {
            "total": total,
            "truncated": total > PREVIEW_USER_SAMPLE_LIMIT,
            "sample_limit": PREVIEW_USER_SAMPLE_LIMIT,
            "list": users,
        },
        "notes": DEPT_PREVIEW_NOTES,
    }


def get_role_preview(role_obj: UserRole, operator: UserInfo) -> dict:
    """角色维度授权预览载荷（授权菜单树 / 字段授权 / 持有用户采样）。

    持有用户列表经调用者数据权限过滤（不泄漏调用者不可见的用户）。
    """
    menu_tree = _serialize_menu_tree(role_obj.menu.all())
    field_permissions = []
    queryset = (
        FieldPermission.objects.filter(role=role_obj).select_related("menu__meta").prefetch_related("field__parent")
    )
    for fp in queryset:
        field_permissions.append(
            {
                "menu": {"pk": str(fp.menu.pk), "title": fp.menu.meta.title},
                "models": _field_groups(fp),
            }
        )

    users_queryset = get_filter_queryset(UserInfo.objects.filter(roles=role_obj), operator)
    total = users_queryset.count()
    users = [
        {
            "pk": str(user.pk),
            "username": user.username,
            "nickname": user.nickname,
            "dept": {"pk": str(user.dept.pk), "name": user.dept.name} if user.dept else None,
            "is_active": user.is_active,
        }
        for user in users_queryset[:PREVIEW_USER_SAMPLE_LIMIT]
    ]
    return {
        "role": {
            "pk": str(role_obj.pk),
            "name": role_obj.name,
            "code": role_obj.code,
            "is_active": role_obj.is_active,
        },
        "menu_tree": menu_tree,
        "field_permissions": field_permissions,
        "users": {
            "total": total,
            "truncated": total > PREVIEW_USER_SAMPLE_LIMIT,
            "sample_limit": PREVIEW_USER_SAMPLE_LIMIT,
            "list": users,
        },
    }
