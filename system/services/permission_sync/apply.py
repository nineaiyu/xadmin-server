#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""菜单权限点同步内核：落库创建、绑定校正与授权。"""

from typing import Any

from django.db import transaction

from common.utils import get_logger
from identity.services import UserRole
from system.models import Menu, MenuMeta, ModelLabelField
from system.utils.platform.menu import get_related_models

from .constants import CRUD_BIND_ACTIONS, IMPORT_EXPORT_ACTIONS, RUNTIME_PERMISSION_PREFIXES
from .types import BindingFix

logger = get_logger(__name__)


def apply_plans(plans: Any, user: Any = None) -> Any:
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


def related_model_labels(view_cls: Any) -> Any:
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


def _role_root_pks(labels: Any) -> Any:
    return set(
        ModelLabelField.objects.filter(
            field_type=ModelLabelField.FieldChoices.ROLE, parent=None, name__in=list(labels)
        ).values_list("pk", flat=True)
    )


def plan_binding_fixes(routes: Any, perms: Any) -> Any:
    """规划模型绑定校正：CRUD 补绑定 + 陈旧绑定收缩；导入导出链清空绑定。

    陈旧绑定（``current - expected``）是视图数据源模型迁移后的失效残留：它在字段权限
    补齐时会被当作有效绑定，把「白名单与当前模型脱节」掩盖成"已配置"（见
    ``audit_field_permissions`` 的判定口径），因此与补绑定一并校正。
    """
    route_by_path = {}
    for route in routes:
        route_by_path[route.url] = route
        route_by_path[route.url.rstrip("$")] = route
    fixes = []
    for perm in perms:
        if str(perm.pk).startswith(RUNTIME_PERMISSION_PREFIXES):
            # 运行时模块权限点（演示示例等）：绑定由模块自身的种子命令维护
            # （如示例图书的「变更历史」刻意不绑模型，复用系统端点会让扫描误判为 CRUD 动作）
            continue
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
            if not expected:
                # 视图无模型（APIView / 自定义端点）：无法判定陈旧，不触动手工绑定
                continue
            missing = expected - current
            stale = current - expected
            if missing or stale:
                mode = "shrink" if stale and not missing else ("add" if missing and not stale else "adjust")
                fixes.append(
                    BindingFix(
                        menu=perm,
                        action=action,
                        mode=mode,
                        current=current,
                        # 保留仍相关的既有绑定 + 补齐缺失；等价于 expected
                        expected=(current | expected) - stale,
                    )
                )
        elif action in IMPORT_EXPORT_ACTIONS and current:
            fixes.append(BindingFix(menu=perm, action=action, mode="clear", current=current, expected=set()))
    return fixes


def apply_binding_fixes(fixes: Any) -> Any:
    with transaction.atomic():
        for fix in fixes:
            fix.menu.model.set(fix.expected)
    return len(fixes)


def model_field_nodes(menus: Any) -> Any:
    """按权限点取「绑定模型（角色字段树根节点）下全部字段节点」，无绑定的返回空表。

    字段白名单的取值面就是权限点 ``Menu.model`` 指向的模型：字段权限是角色×权限点
    ×字段三元组，字段节点取自角色字段树（``field_type=ROLE``），与运行时装载
    （``get_user_field_queryset`` 按 ``field__parent__name + field__name`` 取值）同源。
    """
    root_pks = set()
    for menu in menus:
        root_pks.update(menu.model.filter(field_type=ModelLabelField.FieldChoices.ROLE).values_list("pk", flat=True))
    grouped: dict[Any, list[Any]] = {}
    if not root_pks:
        return {}
    for node in ModelLabelField.objects.filter(
        parent__in=root_pks, field_type=ModelLabelField.FieldChoices.ROLE
    ).order_by("name"):
        grouped.setdefault(node.parent_id, []).append(node)
    result = {}
    for menu in menus:
        fields = []
        for root in menu.model.all():
            fields.extend(grouped.get(root.pk, []))
        result[menu.pk] = fields
    return result


def fill_field_permissions(roles: Any = None) -> Any:
    """为「已获模型权限点、但白名单未覆盖该模型」的组合补齐该模型的全字段白名单。

    字段权限为 fail-closed 的零字段口径（见 ``common/core/serializers.py`` 的
    get_allow_fields）：角色在某权限点上没有覆盖绑定模型的白名单（无行 / 行为空 /
    白名单与绑定模型完全脱节）时，列表/详情输出空对象。演示账号与内置角色的白名单
    依赖种子静态列举，页面/权限点新增、视图模型迁移后容易漏配，本函数按缺口审计
    结果补齐——只补缺口；白名单已覆盖绑定模型字段的组合（含部分裁剪、或同时残留
    旧模型字段的情况）不覆盖，保证管理员在角色页的字段裁剪配置不被改写。

    ``roles`` 限定角色范围（默认全部启用角色）。返回补齐的 ``[(role, menu), ...]``。
    """
    from system.models import FieldPermission

    from .audit import audit_field_permissions

    items = audit_field_permissions(roles=roles)
    if not items:
        return []
    fields_by_menu = model_field_nodes([menu for _role, menu in items])
    filled = []
    with transaction.atomic():
        for role, menu in items:
            fields = fields_by_menu.get(menu.pk) or []
            if not fields:
                continue
            fp, _created = FieldPermission.objects.get_or_create(role=role, menu=menu)
            fp.field.set(fields)
            filled.append((role, menu))
    if filled:
        logger.info(f"filled field permissions: {len(filled)} (role, menu) pairs")
    return filled


def grant_to_roles(created_menus: Any, perms: Any, parent_of_created: Any) -> Any:
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
