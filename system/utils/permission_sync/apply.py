#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""菜单权限点同步内核：落库创建、绑定校正与授权。"""

from django.db import transaction

from common.utils import get_logger
from system.models import Menu, MenuMeta, ModelLabelField, UserRole
from system.utils.menu import get_related_models

from .constants import CRUD_BIND_ACTIONS, IMPORT_EXPORT_ACTIONS
from .types import BindingFix

logger = get_logger(__name__)


def apply_plans(plans, user=None):
    """创建权限点（Menu + MenuMeta）。

    同名已存在时：路径一致但方法不同 → 修正方法（如 `batchDestroy:SystemDataMaskRule`
    种子方法误写为 DELETE，实际路由为 POST）；路径也不同 → 记冲突跳过。
    返回 (created, conflicts, method_fixed)。
    """
    created, conflicts, method_fixed = [], [], []
    with transaction.atomic():
        for plan in plans:
            existing = Menu.objects.filter(name=plan.code, deleted_at__isnull=True).first()
            if existing:
                if existing.path == plan.url and (existing.method or "").upper() != plan.method:
                    existing.method = plan.method
                    existing.save(update_fields=["method", "updated_time"])
                    method_fixed.append(existing)
                else:
                    conflicts.append(plan)
                continue
            meta = MenuMeta.objects.create(title=(plan.description or plan.code)[:250], creator=user, modifier=user)
            menu = Menu.objects.create(
                name=plan.code,
                rank=plan.rank,
                path=plan.url,
                method=plan.method,
                menu_type=Menu.MenuChoices.PERMISSION,
                parent_id=plan.parent_id,
                meta=meta,
                is_active=True,
                creator=user,
                modifier=user,
            )
            if plan.model_pks:
                menu.model.set(plan.model_pks)
            created.append(menu)
    return created, conflicts, method_fixed


def related_model_labels(view_cls):
    try:
        model = view_cls.queryset.model
    except Exception as e:  # noqa: BLE001 无 queryset 的视图（APIView/自定义）不参与绑定
        logger.debug(f"view has no queryset, skip model binding: {view_cls} {e}")
        return set()
    try:
        return set(get_related_models(model))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"get related models failed: {view_cls} {e}")
        return set()


def _role_root_pks(labels):
    return set(
        ModelLabelField.objects.filter(
            field_type=ModelLabelField.FieldChoices.ROLE, parent=None, name__in=list(labels)
        ).values_list("pk", flat=True)
    )


def plan_binding_fixes(routes, perms):
    """规划模型绑定校正：CRUD 补绑定（只增不减）；导入导出链清空绑定。"""
    route_by_path = {}
    for route in routes:
        route_by_path[route.url] = route
        route_by_path[route.url.rstrip("$")] = route
    fixes = []
    for perm in perms:
        # 权限点路径两种写法并存（种子多带 `$`，生成器产物也带），两种都试
        route = route_by_path.get(perm.path) or route_by_path.get(f"{perm.path}$")
        if not route:
            continue
        action = (route.actions or {}).get((perm.method or "").lower())
        if not action:
            continue
        current = set(perm.model.values_list("pk", flat=True))
        if action in CRUD_BIND_ACTIONS:
            expected = _role_root_pks(related_model_labels(route.view_cls))
            if expected - current:
                fixes.append(
                    BindingFix(menu=perm, action=action, mode="add", current=current, expected=current | expected)
                )
        elif action in IMPORT_EXPORT_ACTIONS and current:
            fixes.append(BindingFix(menu=perm, action=action, mode="clear", current=current, expected=set()))
    return fixes


def apply_binding_fixes(fixes):
    with transaction.atomic():
        for fix in fixes:
            fix.menu.model.set(fix.expected)
    return len(fixes)


def grant_to_roles(created_menus, perms, parent_of_created):
    """把新建权限点授予「已拥有同模块权限点且拥有父菜单」的角色（可选，默认不执行）。"""
    granted = []
    for menu in created_menus:
        parent_id = parent_of_created.get(menu.pk)
        sibling_ids = [perm.pk for perm in perms if perm.parent_id == parent_id]
        if not sibling_ids:
            continue
        roles = UserRole.objects.filter(is_active=True, menu__pk__in=sibling_ids)
        if parent_id:
            roles = roles.filter(menu__pk=parent_id)
        for role in roles.distinct():
            role.menu.add(menu)
            granted.append((role.name, menu.name))
    return granted
