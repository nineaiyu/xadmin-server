#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""权限可视化：菜单/API/数据/字段权限的直查（不经缓存）。"""

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from common.base.utils import menu_list_to_tree
from common.core.permission import get_user_menu_queryset
from system.models import DataPermission, FieldPermission, Menu, ModelLabelField, UserInfo
from system.utils.rule_meta import DATA_PERMISSION_SEMANTIC_NOTE

from .decode import decode_data_permission
from .labels import _new_label_cache


def get_user_menu_queryset_for_preview(user_obj: UserInfo):
    """预览用可见菜单：超管全量旁路（与 routes 视图一致），无角色/部门 → None。"""
    if user_obj.is_superuser:
        return Menu.objects.filter(is_active=True)
    return get_user_menu_queryset(user_obj)


def _visible_menu_or_error(user_obj: UserInfo, menu_pk, menu_type=None) -> Menu:
    """菜单 pk → 目标用户可见范围内的菜单对象（非法/不可见统一 400，不抛 500）。

    非法 UUID 在 filter 时抛 Django ValidationError，需就地归一为业务错误。
    """
    menu_queryset = get_user_menu_queryset_for_preview(user_obj)
    if not menu_queryset:
        raise ValidationError("菜单不在目标用户可见范围")
    try:
        queryset = menu_queryset.select_related("meta").filter(pk=menu_pk)
        if menu_type is not None:
            queryset = queryset.filter(menu_type=menu_type)
        menu_obj = queryset.first()
    except (DjangoValidationError, ValueError, TypeError):
        menu_obj = None
    if menu_obj is None:
        raise ValidationError("菜单不在目标用户可见范围")
    return menu_obj


def _serialize_menu_tree(menus) -> list:
    """页面菜单（目录/菜单）→ 前端只读树（按 rank 排序）。

    刻意不走 RouteSerializer（BaseModelSerializer 会按请求者字段权限裁剪，
    无白名单时字段被裁空导致树构建失败），改为手动投影固定字段。
    menu_list_to_tree 要求父节点在同一集合内，否则孤儿子节点会被当成根，
    故先按全量 pk→parent_id 映射补齐祖先（一次查询，避免逐层 N+1）。
    """
    if not menus:
        return []
    ordered = list(menus.select_related("meta", "parent"))
    parent_map = dict(Menu.objects.values_list("pk", "parent_id"))
    pks = {str(menu.pk) for menu in ordered}
    missing = set()
    for menu in ordered:
        current = parent_map.get(menu.pk)
        while current is not None and str(current) not in pks and str(current) not in missing:
            missing.add(str(current))
            current = parent_map.get(current)
    if missing:
        ordered.extend(Menu.objects.filter(pk__in=missing).select_related("meta", "parent"))
    data = [
        {
            "pk": str(menu.pk),
            "name": menu.name,
            "rank": menu.rank,
            "path": menu.path,
            "menu_type": menu.menu_type,
            "parent": {"pk": str(menu.parent.pk), "name": menu.parent.name} if menu.parent else None,
            "title": menu.meta.title if menu.meta else menu.name,
        }
        for menu in ordered
    ]
    data.sort(key=lambda item: item.get("rank") or 0)
    return menu_list_to_tree(data, "parent")


def _count_menu_nodes(tree: list) -> int:
    """菜单树节点总数（含各层子节点；summary.menu_count 需与页面显示一致）。"""
    return sum(1 + _count_menu_nodes(node.get("children") or []) for node in tree)


def get_user_api_permissions(user_obj: UserInfo) -> list:
    """API 权限码：可见菜单中 menu_type=PERMISSION 的全量码（不经 24h 缓存）。"""
    menu_queryset = get_user_menu_queryset_for_preview(user_obj)
    if not menu_queryset:
        return []
    return [
        {
            "menu_pk": str(menu.pk),
            "code": menu.name,
            "method": menu.method,
            "path": menu.path,
            "title": menu.meta.title,
        }
        for menu in menu_queryset.filter(menu_type=Menu.MenuChoices.PERMISSION).select_related("meta")
    ]


def get_user_data_permissions(user_obj: UserInfo) -> dict:
    """数据权限明细：个人授权 + 部门祖先链逐层分组（语义对齐 get_filter_queryset）。

    与运行时口径对齐的两个关键点（否则会出现「预览显示有授权、实际看不到」）：
    1. 只有**启用部门**上的授权参与过滤（filter.py 只把 active 部门加入 active_chain），
       停用层仍列出以便定位，但标记 is_active/effective=False 且不计入 has_any_grant；
    2. 绑定菜单的授权（menu_scoped）只在对应菜单上下文生效，通用列表接口下不生效，
       同样不计入 has_any_grant，由展示层提示。
    """
    label_cache = _new_label_cache()
    personal = [
        decode_data_permission(dp, user_obj, label_cache)
        for dp in DataPermission.objects.filter(is_active=True).filter(userinfo=user_obj).prefetch_related("menu__meta")
    ]
    dept_chain = []
    has_any_grant = any(not item["menu_scoped"] for item in personal)
    if user_obj.dept:
        # 与 filter.py 的祖先链口径一致（含自身，向上递归）；按部门链顺序（自身→上级）展示
        chain = [user_obj.dept]
        seen = {str(user_obj.dept.pk)}
        current = user_obj.dept
        while current.parent and str(current.parent.pk) not in seen:
            current = current.parent
            seen.add(str(current.pk))
            chain.append(current)
        for dept in chain:
            permissions = [
                decode_data_permission(dp, user_obj, label_cache)
                for dp in DataPermission.objects.filter(is_active=True)
                .filter(deptinfo=dept)
                .prefetch_related("menu__meta")
            ]
            effective = bool(dept.is_active)
            if effective and any(not item["menu_scoped"] for item in permissions):
                has_any_grant = True
            dept_chain.append(
                {
                    "dept": {"pk": str(dept.pk), "name": dept.name},
                    "relation": "self" if dept.pk == user_obj.dept.pk else "ancestor",
                    "is_active": effective,
                    "effective": effective,
                    "permissions": permissions,
                }
            )
    return {
        "enabled": settings.PERMISSION_DATA_ENABLED,
        "superuser_bypass": user_obj.is_superuser,
        "has_any_grant": has_any_grant,
        "personal": personal,
        "dept_chain": dept_chain,
        "semantic_note": DATA_PERMISSION_SEMANTIC_NOTE,
    }


def _field_groups(field_permission: FieldPermission) -> list:
    """FieldPermission.field M2M → 按父模型分组的字段结构。"""
    models = {}
    for field in field_permission.field.all().select_related("parent"):
        parent = field.parent
        if not parent:
            continue
        group = models.setdefault(
            parent.name,
            {
                "model": parent.name,
                "model_label": parent.label,
                "fields": [],
                "field_labels": [],
            },
        )
        group["fields"].append(field.name)
        group["field_labels"].append(field.label)
    return list(models.values())


def get_user_field_matrix(user_obj: UserInfo) -> list:
    """字段权限矩阵（菜单 × 角色 × 模型 → 字段白名单）。

    复刻 get_user_field_queryset 的取数范围（用户角色 ∪ 部门挂载角色），
    但直查并保留完整关联关系，不经 10s 缓存。
    """
    q = Q()
    has_q = False
    if user_obj.roles.exists():
        q |= Q(role__in=user_obj.roles.all()) & Q(role__is_active=True)
        has_q = True
    if user_obj.dept:
        q |= Q(role__deptinfo=user_obj.dept) & Q(role__deptinfo__is_active=True)
        has_q = True
    if not has_q:
        return []
    rows = []
    queryset = FieldPermission.objects.filter(q).select_related("menu__meta", "role").prefetch_related("field__parent")
    for fp in queryset:
        for group in _field_groups(fp):
            rows.append(
                {
                    "menu": {"pk": str(fp.menu.pk), "title": fp.menu.meta.title},
                    "role": {"pk": str(fp.role.pk), "name": fp.role.name},
                    **group,
                }
            )
    return rows


def get_trial_candidates() -> list:
    """试算模型候选：数据权限注册表（ModelLabelField DATA 根节点）+ 规则命中标记。"""
    registered_tables = set()
    for rules_json in DataPermission.objects.filter(is_active=True).values_list("rules", flat=True):
        for rule in rules_json or []:
            if isinstance(rule, dict) and rule.get("table"):
                registered_tables.add(rule["table"])
    return [
        {
            "label": node.name,
            "display": f"{node.label} ({node.name})",
            "has_rules": node.name in registered_tables,
        }
        for node in ModelLabelField.objects.filter(
            field_type=ModelLabelField.FieldChoices.DATA, parent__isnull=True
        ).exclude(name="*")
    ]
