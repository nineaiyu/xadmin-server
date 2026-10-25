#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""菜单权限点批量生成内核（菜单页「自动添加API权限」的构建/落库/预览）。

与 ``apply.py`` 的分工：apply 消费扫描产物（``PlanItem``，命令行同步链），
本模块消费视图声明的权限清单（``get_view_permissions`` 形态，菜单页交互链）。
预览与执行共用 ``build_permission_items``，保证「所见即所得」。
"""

from typing import Any

from django.db import transaction
from django.utils import timezone

from system.models import Menu, MenuMeta, ModelLabelField

from .types import PERMISSION_MENU_RANK_BASE


def build_permission_items(
    instance: Any, permissions: Any, skip_existing: Any
) -> list[tuple[str, Any, dict[str, Any]]]:
    """构造待写入的权限点（只读，不落库）。

    返回 ``(action, 已有菜单或 None, 数据)`` 列表：``action=create`` 为新建（标题前缀 C-），
    ``update`` 为覆盖既有权限点（前缀 U-）；``skip_existing`` 命中时整体跳过——
    预览与执行共用本方法，保证「所见即所得」。

    既有权限点与角色模型清单各以一次 ``__in`` 批量查询取回，避免逐权限点回表；
    meta 一并 select_related，供覆盖更新时直接改标题。
    """
    permissions = list(permissions)
    if not permissions:
        return []
    existing = {
        menu.name: menu
        for menu in Menu.objects.filter(
            menu_type=Menu.MenuChoices.PERMISSION,
            name__in=[permission.get("code") for permission in permissions],
        ).select_related("meta")
    }
    role_models: dict[str, list[Any]] = {}
    role_labels = {label for permission in permissions for label in permission.get("models")}
    if role_labels:
        fields = ModelLabelField.objects.filter(field_type=ModelLabelField.FieldChoices.ROLE, name__in=role_labels)
        for field in fields:
            role_models.setdefault(field.name, []).append(field)

    items = []
    rank = PERMISSION_MENU_RANK_BASE
    for permission in permissions:
        rank += 1
        models = [field for label in permission.get("models") for field in role_models.get(label, [])]
        data = {
            "rank": rank,
            "is_active": True,
            "menu_type": Menu.MenuChoices.PERMISSION,
            "name": permission.get("code"),
            "parent": instance,
            "path": permission.get("url"),
            "method": permission.get("method"),
            "model": models,
            "meta": {"title": permission.get("description")[:250]},
        }
        permission_menu = existing.get(data["name"])
        if permission_menu and skip_existing:
            continue
        action = "update" if permission_menu else "create"
        data["meta"]["title"] = ("U-" if permission_menu else "C-") + data["meta"]["title"]
        items.append((action, permission_menu, data))
    return items


def serialize_permission_items(items: Any) -> dict[str, Any]:
    """预览载荷：逐条给出动作、权限码、接口与标题，附新建/覆盖计数。"""
    results = [
        {
            "action": action,
            "name": data["name"],
            "path": data["path"],
            "method": data["method"],
            "title": data["meta"]["title"],
        }
        for action, _target, data in items
    ]
    return {
        "results": results,
        "create_count": len([item for item in results if item["action"] == "create"]),
        "update_count": len([item for item in results if item["action"] == "update"]),
    }


def save_permission_items(items: Any, user: Any = None) -> list[Any]:
    """批量落库权限点并返回落库实例。

    信号抑制（menu 的 post_save 缓存清理）由**调用方装饰**（见菜单视图薄委托）：
    服务内核不静态依赖 system.signal_handler，避免「services 包 → 信号模块」
    的静态环（mypy 全仓分析下会把函数内 identity.services 取值降级为 Any）。

    覆盖更新与新创建各走一支批量写入，整个落库过程原子提交。若逐条保存触发信号，
    会按每个权限点各自扫一遍用户/角色/部门（同一批内重复扫描）；改为收集实例后
    一次精确失效，保证「被覆盖更新的子权限点」也进失效集——只失效父菜单会让
    这些用户最长 24h 持旧权限（路由缓存 TTL）。creator/modifier 原由全局 pre_save
    信号按请求用户补齐，批量写入不触发信号，按同一口径显式赋值。
    """
    # 同一批出现重复权限码时按顺序收敛：覆盖更新后写生效（与逐条保存一致），新建先到先得
    updates: dict[str, Any] = {}
    creates: dict[str, Any] = {}
    for _action, permission_menu, data in items:
        if permission_menu is not None:
            updates[permission_menu.pk] = (permission_menu, data)
        else:
            creates.setdefault(data["name"], data)
    with transaction.atomic():
        saved = _update_permission_items(list(updates.values()), user)
        saved += _create_permission_items(list(creates.values()), user)
    return saved


def _update_permission_items(updates: Any, user: Any) -> list[Any]:
    """覆盖既有权限点：菜单行与 meta 标题各一次批量 UPDATE，角色模型按差集同步。"""
    if not updates:
        return []
    updated_time = timezone.now()
    metas = []
    for permission_menu, data in updates:
        for field in ("rank", "is_active", "menu_type", "name", "parent", "path", "method"):
            setattr(permission_menu, field, data[field])
        permission_menu.modifier = user
        permission_menu.updated_time = updated_time
        meta = permission_menu.meta
        meta.title = data["meta"]["title"]
        meta.modifier = user
        meta.updated_time = updated_time
        metas.append(meta)
    menus = [menu for menu, _data in updates]
    Menu.objects.bulk_update(
        menus,
        fields=["rank", "is_active", "menu_type", "name", "parent", "path", "method", "modifier", "updated_time"],
    )
    MenuMeta.objects.bulk_update(metas, fields=["title", "modifier", "updated_time"])
    sync_permission_models([(menu, data["model"]) for menu, data in updates])
    return menus


def _create_permission_items(creates: Any, user: Any) -> list[Any]:
    """新建权限点：meta 先批量落库取得主键回填 meta_id，菜单行随后批量落库。

    活跃行名称唯一约束兜底并发冲突，冲突行被数据库跳过；按主键回读实际
    落库实例，角色模型关联只建给真实存在的菜单行。
    """
    if not creates:
        return []
    dept = getattr(user, "dept", None)
    models_by_name = {data["name"]: data["model"] for data in creates}
    metas = MenuMeta.objects.bulk_create(
        [MenuMeta(title=data["meta"]["title"], creator=user, modifier=user, dept_belong=dept) for data in creates]
    )
    menus = Menu.objects.bulk_create(
        [
            Menu(
                rank=data["rank"],
                is_active=data["is_active"],
                menu_type=data["menu_type"],
                name=data["name"],
                parent=data["parent"],
                path=data["path"],
                method=data["method"],
                meta=meta,
                creator=user,
                modifier=user,
                dept_belong=dept,
            )
            for data, meta in zip(creates, metas, strict=True)
        ],
        ignore_conflicts=True,
    )
    # ignore_conflicts 无法区分被跳过的冲突行，按主键回读实际落库实例
    saved = list(Menu.objects.filter(pk__in=[menu.pk for menu in menus]).select_related("meta"))
    if saved:
        Menu.model.through.objects.bulk_create(
            [
                Menu.model.through(menu=menu, modellabelfield=field)
                for menu in saved
                for field in models_by_name.get(menu.name, [])
            ]
        )
    return saved


def sync_permission_models(menu_model_pairs: Any) -> None:
    """把 ``[(menu, models)]`` 的角色模型关联同步进中间表（等价于逐实例 ``set()``）。

    现有关联一次读回后按差集增删，避免每权限点各查一遍。
    """
    if not menu_model_pairs:
        return
    through = Menu.model.through
    menu_ids = [menu.pk for menu, _models in menu_model_pairs]
    rows = list(through.objects.filter(menu_id__in=menu_ids).values_list("pk", "menu_id", "modellabelfield_id"))
    desired = {(menu.pk, field.pk) for menu, models in menu_model_pairs for field in models}
    current = {(menu_id, field_id) for _pk, menu_id, field_id in rows}
    stale_pks = [pk for pk, menu_id, field_id in rows if (menu_id, field_id) not in desired]
    fresh_pairs = desired - current
    if stale_pks:
        through.objects.filter(pk__in=stale_pks).delete()
    if fresh_pairs:
        through.objects.bulk_create(
            [through(menu_id=menu_id, modellabelfield_id=field_id) for menu_id, field_id in fresh_pairs]
        )
