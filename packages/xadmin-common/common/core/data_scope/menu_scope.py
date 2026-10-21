#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""数据权限菜单上下文解析：显式 action 覆盖 + 同资源 list 缺省回退。

请求命中的 ``request.user.menu`` 是单个接口权限点。数据授权按以下口径选择：

- 通用授权（``menu`` 为空）始终参与；
- 当前 action 存在**显式绑定且对当前模型可编译**的授权时，只使用「通用 + 当前 action」，
  同资源 list 授权不参与（严格互不影响）；
- 当前 action 没有可用显式授权时，回退到「通用 + 同资源 list 权限点」，
  「列表能看到的行才能打开详情」；
- 选中集合内部仍按各授权自身的且/或模式、授权之间取并集（最宽生效）。

页面/目录上下文（数据权限「试算」传页面菜单）解析为其下 ``list:*`` 权限点，
与运行时列表请求的菜单上下文同口径。

解析结果按用户对象缓存（``request.user`` 与请求同生命周期），避免一次请求内
多次 ``get_filter_queryset`` 重复查菜单。
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from common.contracts import Menu
from common.utils import get_logger

logger = get_logger(__name__)

_SCOPE_CACHE_ATTR = "_data_scope_menu_scope"


@dataclass(frozen=True)
class MenuScope:
    """当前请求的数据授权菜单范围。

    - ``exact_pks``：显式绑定当前接口（页面/目录上下文为其下接口）的菜单主键；
    - ``fallback_pks``：没有可用显式授权时回退的同资源 list 权限点。
    """

    exact_pks: frozenset[str] = frozenset()
    fallback_pks: frozenset[str] = frozenset()

    @property
    def candidate_pks(self) -> frozenset[str]:
        return self.exact_pks | self.fallback_pks


def _load_menu(menu_pk: Any) -> tuple[Any, Any] | None:
    """菜单名与类型；查询失败返回 None（调用方回落为仅当前菜单）。"""
    try:
        row = Menu.objects.filter(pk=menu_pk, is_active=True).values_list("name", "menu_type").first()
        typed_row: tuple[Any, Any] | None = row
        return typed_row
    except Exception:
        # 菜单表不可用不应把数据权限放大：回落为仅当前菜单（与旧口径一致）
        logger.warning("resolve data scope menu context failed. menu:%s", menu_pk, exc_info=True)
        return None


def _load_list_menu_pk(resource: str) -> Any:
    """同资源 list 权限点主键；不存在（自定义页面/非 ViewSet 端点）返回 None。"""
    try:
        return (
            Menu.objects.filter(name=f"list:{resource}", menu_type=Menu.MenuChoices.PERMISSION, is_active=True)
            .values_list("pk", flat=True)
            .first()
        )
    except Exception:
        logger.warning("resolve data scope list menu failed. resource:%s", resource, exc_info=True)
        return None


def _split_action(name: Any) -> tuple[str, str | None]:
    """菜单名拆为 ``(action, resource)``；非 ``action:Resource`` 形态 resource 为 None。"""
    text = str(name or "")
    if ":" not in text:
        return text, None
    action, resource = text.split(":", 1)
    return action, resource


def _descendant_permission_pks(menu_pk: Any, *, list_only: bool = False) -> frozenset[str]:
    """页面/目录下的权限点主键（含嵌套目录；仅启用菜单）。

    ``list_only`` 只取 ``list:*``（运行时菜单上下文）；否则取全部权限点
    （草稿页面绑定的保存展开口径）。
    """
    try:
        rows = list(Menu.objects.filter(is_active=True).values_list("pk", "parent_id", "name", "menu_type"))
    except Exception:
        logger.warning("resolve data scope descendant menus failed. menu:%s", menu_pk, exc_info=True)
        return frozenset()

    children: dict[str, list[tuple[str, Any, Any]]] = defaultdict(list)
    for pk, parent_id, name, menu_type in rows:
        children[str(parent_id)].append((str(pk), name, menu_type))

    result: set[str] = set()
    pending = [str(menu_pk)]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        for pk, name, menu_type in children.get(current, []):
            if menu_type == Menu.MenuChoices.PERMISSION:
                if not list_only or str(name).startswith("list:"):
                    result.add(pk)
            else:
                pending.append(pk)
    return frozenset(result)


def binding_permission_pks(binding_pk: Any) -> frozenset[str]:
    """数据权限绑定菜单覆盖的权限点集合。

    - 权限点：自身；
    - 页面/目录：其下全部权限点（页面勾选保存时的展开口径）。
    """
    if not binding_pk:
        return frozenset()
    key = str(binding_pk)
    row = _load_menu(binding_pk)
    if row is None:
        return frozenset({key})
    _, menu_type = row
    if menu_type == Menu.MenuChoices.PERMISSION:
        return frozenset({key})
    return _descendant_permission_pks(binding_pk)


def menu_binding_matches_context(binding_pk: Any, context_pk: Any) -> bool:
    """数据权限绑定的菜单是否命中当前上下文（草稿试算复用）。"""
    if not binding_pk or not context_pk:
        return False
    if str(binding_pk) == str(context_pk):
        return True
    context = resolve_menu_scope(context_pk)
    if context is None:
        return False
    return bool(binding_permission_pks(binding_pk) & context.candidate_pks)


def resolve_menu_scope(menu_pk: Any) -> MenuScope | None:
    """按菜单主键解析数据授权范围。

    - 接口权限点：``exact={自身}``；非 list 动作回退同资源 list（``fallback={list}``）；
    - 页面/目录：``exact=fallback=其下 list 权限点``（试算上下文）；
    - 无菜单上下文：返回 None，调用方只应用通用授权。
    """
    if not menu_pk:
        return None

    key = str(menu_pk)
    row = _load_menu(menu_pk)
    if row is None:
        return MenuScope(exact_pks=frozenset({key}))

    name, menu_type = row
    if menu_type != Menu.MenuChoices.PERMISSION:
        pks = _descendant_permission_pks(menu_pk, list_only=True)
        return MenuScope(exact_pks=pks, fallback_pks=pks)

    action, resource = _split_action(name)
    if action == "list" or not resource:
        return MenuScope(exact_pks=frozenset({key}))

    list_pk = _load_list_menu_pk(resource)
    if list_pk:
        return MenuScope(exact_pks=frozenset({key}), fallback_pks=frozenset({str(list_pk)}))
    return MenuScope(exact_pks=frozenset({key}))


def resolve_menu_scope_pks(menu_pk: Any) -> frozenset[str] | None:
    """兼容入口：返回候选菜单主键集合（exact + fallback）。"""
    scope = resolve_menu_scope(menu_pk)
    return scope.candidate_pks if scope else None


def resolve_scope_for_user(user_obj: Any) -> MenuScope | None:
    """返回本次数据权限过滤的菜单范围（按用户对象缓存）。"""
    menu_pk = getattr(user_obj, "menu", None)
    if not menu_pk:
        return None

    key = str(menu_pk)
    cached = getattr(user_obj, _SCOPE_CACHE_ATTR, None)
    if cached is not None and cached[0] == key:
        typed_cached: MenuScope | None = cached[1]
        return typed_cached

    result = resolve_menu_scope(menu_pk)
    try:
        setattr(user_obj, _SCOPE_CACHE_ATTR, (key, result))
    except Exception:
        # 自定义 user 对象可能禁止写属性；退回每次重算，不影响正确性
        pass
    return result


def resolve_scope_menu_pks(user_obj: Any) -> frozenset[str] | None:
    """兼容入口：返回当前用户数据授权候选菜单主键集合。"""
    scope = resolve_scope_for_user(user_obj)
    return scope.candidate_pks if scope else None


def _menu_ids(grant: Any) -> frozenset[str]:
    return getattr(grant, "menu_ids", frozenset())


def select_compiled_grants(compiled: list[tuple[Any, Any]], scope: MenuScope | None) -> list[tuple[Any, Any]]:
    """按「显式 action 覆盖 + list 缺省回退」选择参与合并的授权。

    ``compiled`` 为 ``[(grant, ScopeResult|None)]``；只返回编译成功的项。
    显式授权是否存在，以「绑定当前 action 且对当前模型可编译」为准——
    绑了当前 action 但规则只针对其它模型的授权不会掐掉 list 回退。
    """
    if scope is None:
        return [(grant, result) for grant, result in compiled if result is not None]

    exact_applicable = any(
        result is not None and bool(_menu_ids(grant) & scope.exact_pks) for grant, result in compiled
    )
    target = scope.exact_pks if exact_applicable else scope.fallback_pks
    if not target:
        return [(grant, result) for grant, result in compiled if result is not None and not _menu_ids(grant)]
    return [
        (grant, result)
        for grant, result in compiled
        if result is not None and (not _menu_ids(grant) or bool(_menu_ids(grant) & target))
    ]


def scope_mode(compiled: list[tuple[Any, Any]], scope: MenuScope | None) -> str:
    """本次选择实际走的范围：exact / list / generic（试算诊断展示）。"""
    if scope is None or not scope.candidate_pks:
        return "generic"
    if any(result is not None and bool(_menu_ids(grant) & scope.exact_pks) for grant, result in compiled):
        return "exact"
    return "list" if scope.fallback_pks else "generic"
