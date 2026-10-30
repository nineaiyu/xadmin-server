#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""菜单权限点同步内核：落库创建、绑定校正与授权。"""

from typing import Any

from django.db import transaction
from django.db.models import Prefetch

from common.utils import get_logger
from identity.services import UserRole
from system.models import Menu, MenuMeta, ModelLabelField
from system.utils.platform.menu import get_related_models

from .constants import CRUD_BIND_ACTIONS, IMPORT_EXPORT_ACTIONS
from .declarations import RUNTIME_PERMISSION_PREFIXES
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


def _role_bindings_by_menu(menus: Any) -> dict[Any, list[Any]]:
    """批量取「菜单 → 已绑定的角色字段树根节点」，替代逐菜单的 ``menu.model`` 读取。

    ``menu.model`` 是 m2m：逐菜单读取时「取值为 ROLE 的根节点」与「取全部绑定节点」
    各发一条查询，菜单数百时即数百条 N+1 小往返（种子收尾的全量审计每轮都跑这份
    读取）。一次预取把它收敛成 2 条查询，判定口径不变（仍只取 ``field_type=ROLE``）。
    """
    if not menus:
        return {}
    role_bindings = Prefetch(
        "model",
        queryset=ModelLabelField.objects.filter(field_type=ModelLabelField.FieldChoices.ROLE),
        to_attr="role_bindings",
    )
    fetched = {
        menu.pk: menu
        for menu in Menu.objects.filter(pk__in=[menu.pk for menu in menus]).prefetch_related(role_bindings)
    }
    return {menu.pk: getattr(fetched.get(menu.pk), "role_bindings", []) for menu in menus}


def model_field_nodes(menus: Any) -> Any:
    """按权限点取「绑定模型（角色字段树根节点）下全部字段节点」，无绑定的返回空表。

    字段白名单的取值面就是权限点 ``Menu.model`` 指向的模型：字段权限是角色×权限点
    ×字段三元组，字段节点取自角色字段树（``field_type=ROLE``），与运行时装载
    （``get_user_field_queryset`` 按 ``field__parent__name + field__name`` 取值）同源。
    """
    menus = list(menus)
    bindings = _role_bindings_by_menu(menus)
    root_pks = {node.pk for nodes in bindings.values() for node in nodes}
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
        for root in bindings.get(menu.pk, []):
            fields.extend(grouped.get(root.pk, []))
        result[menu.pk] = fields
    return result


def snapshot_role_field_names() -> dict[str, set[str]]:
    """当前 ROLE 字段树快照：``{模型标签: {字段名}}``。

    在 ``sync_model_field()`` **之前**调用，用于识别「本次同步新引入的字段」。
    只统计 ROLE 根节点及其直接子节点（与运行时白名单取值面一致）。
    """
    from system.models import ModelLabelField

    roots = list(
        ModelLabelField.objects.filter(field_type=ModelLabelField.FieldChoices.ROLE, parent__isnull=True).values(
            "pk", "name"
        )
    )
    children: dict[Any, set[str]] = {}
    if roots:
        rows = ModelLabelField.objects.filter(
            parent__in=[root["pk"] for root in roots],
            field_type=ModelLabelField.FieldChoices.ROLE,
        ).values_list("parent_id", "name")
        for parent_id, name in rows:
            children.setdefault(parent_id, set()).add(name)
    return {root["name"]: children.get(root["pk"], set()) for root in roots}


def complete_field_permissions(
    roles: Any = None,
    *,
    dry_run: bool = False,
    only_new: bool = False,
    baseline: dict[str, set[str]] | None = None,
) -> Any:
    """把缺失字段补进已有角色白名单（**只增不减**）。

    新增字段后，旧角色的白名单不会自动包含它；字段权限是 fail-closed 的，表现就是
    新字段在列表/详情/元数据表单里静默消失。本函数按「权限点绑定模型 × 角色」补缺，
    不删除管理员已收敛的字段，可反复执行。

    两种模式：

    - ``only_new=True``（新增字段场景）：只补「本次同步新引入」的字段，需同时传入
      ``baseline``（同步前由 :func:`snapshot_role_field_names` 获取）。角色对某模型
      原本一个字段都没有时不会自动扩权（避免把敏感字段授予未配置该模型的角色）。
    - ``only_new=False``（升级补齐场景，供 ``post_upgrade`` 使用）：补齐绑定模型下
      所有缺失字段，语义与历史升级行为一致。

    ``dry_run`` 只返回待补列表、不写库。返回
    ``[(role, menu, [待补字段节点]), ...]``。
    """
    from system.models import FieldPermission

    if only_new and baseline is None:
        raise ValueError("only_new=True requires a baseline snapshot")

    role_queryset = UserRole.objects.filter(is_active=True).prefetch_related("menu")
    if roles is not None:
        role_queryset = role_queryset.filter(pk__in=[role.pk for role in roles])
    menus = list(
        Menu.objects.filter(menu_type=Menu.MenuChoices.PERMISSION, is_active=True)
        .filter(model__isnull=False)
        .distinct()
    )
    bindings = _role_bindings_by_menu(menus)
    root_pks = {node.pk for nodes in bindings.values() for node in nodes}
    if not root_pks:
        return []
    nodes_by_root: dict[Any, list[Any]] = {}
    for node in ModelLabelField.objects.filter(
        parent__in=root_pks, field_type=ModelLabelField.FieldChoices.ROLE
    ).order_by("name"):
        nodes_by_root.setdefault(node.parent_id, []).append(node)

    completed: list[Any] = []
    for role in role_queryset:
        for menu in role.menu.all():
            roots = bindings.get(menu.pk) or []
            if not roots:
                continue
            permission = FieldPermission.objects.filter(role=role, menu=menu).first()
            existing = set(permission.field.values_list("pk", flat=True)) if permission else set()
            missing: list[Any] = []
            for root in roots:
                fields = nodes_by_root.get(root.pk, [])
                if only_new:
                    # 角色对该模型原本无任何字段：不自动扩权，交由权限点/白名单初始化处理
                    if not any(field.pk in existing for field in fields):
                        continue
                    known = baseline.get(root.name, set()) if baseline else set()
                    missing.extend(field for field in fields if field.name not in known and field.pk not in existing)
                else:
                    missing.extend(field for field in fields if field.pk not in existing)
            if not missing:
                continue
            completed.append((role, menu, missing))
            if dry_run:
                continue
            if permission is None:
                permission = FieldPermission.objects.create(role=role, menu=menu)
            permission.field.add(*missing)
    if completed and not dry_run:
        logger.info("completed field permissions: %d (role, menu) pairs", len(completed))
    return completed


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
    # 按「字段集」分组：同一模型的权限点共享同一份白名单。
    # 曾经的形态是逐组合 ``get_or_create`` + ``.set()``——前者每个组合一次 SELECT、
    # 后者每次 DELETE+INSERT+SELECT 三条往返，角色×菜单数百时即数千条小查询，
    # 与读取侧 N+1 叠加后把种子收尾拖到分钟级（全量 pytest 尾部假死的直接燃料）。
    # 现在：一次查出全部已存在的组合（1 条 SQL），只对缺口逐个 create
    # （FieldPermission 的 id 由 save 路径拼装，bulk_create 拿不到，不能走批量插入）；
    # m2m 按组「一条 DELETE + 一条多值 INSERT」替代逐组合三连。
    groups: dict[tuple[int, ...], list[tuple[Any, Any, list[Any]]]] = {}
    for role, menu in items:
        fields = fields_by_menu.get(menu.pk) or []
        if not fields:
            continue
        key = tuple(sorted(field.pk for field in fields))
        groups.setdefault(key, []).append((role, menu, fields))
    if not groups:
        return []
    pairs = [(role, menu) for group in groups.values() for role, menu, _fields in group]
    existing = {
        (fp.role_id, fp.menu_id): fp
        for fp in FieldPermission.objects.filter(
            role__in={role for role, _menu in pairs}, menu__in={menu for _role, menu in pairs}
        )
    }
    filled = []
    through = FieldPermission.field.through
    with transaction.atomic():
        # 全部缺口一次性重写白名单（与逐组 .set() 同语义：先清后插）。分组只用于
        # 复用字段清单，DELETE 与 INSERT 都按「全部目标组合」合并成各一条多值语句——
        # 逐组发语句时，角色×菜单上百个缺口就是几百条 DELETE/INSERT 小往返。
        pks: list[Any] = []
        relations: list[tuple[Any, Any]] = []
        for group in groups.values():
            fields = group[0][2]
            for role, menu, _fields in group:
                fp = existing.get((role.pk, menu.pk))
                if fp is None:
                    fp = FieldPermission.objects.create(role=role, menu=menu)
                    existing[(role.pk, menu.pk)] = fp
                pks.append(fp.pk)
                relations.extend((fp.pk, field.pk) for field in fields)
                filled.append((role, menu))
        if pks:
            through.objects.filter(fieldpermission_id__in=pks).delete()
            through.objects.bulk_create(
                [through(fieldpermission_id=pk, modellabelfield_id=field_pk) for pk, field_pk in relations],
                ignore_conflicts=True,
                batch_size=1000,
            )
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
