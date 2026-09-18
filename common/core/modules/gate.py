#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""功能模块注册表：禁用模块的路由/菜单/权限点裁剪。"""

import re
from functools import lru_cache

from django.db.models import Q

from common.utils import get_logger

from .registry import module_index, resolve_modules
from .specs import MENU_TYPE_DIRECTORY, MENU_TYPE_PERMISSION

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _disabled_specs() -> tuple:
    resolution = resolve_modules()
    index = module_index()
    return tuple(index[mid] for mid in sorted(resolution.disabled))


@lru_cache(maxsize=1)
def _disabled_route_regexes() -> tuple:
    patterns = []
    for spec in _disabled_specs():
        for prefix in spec.routes:
            patterns.append(re.compile(prefix))
    return tuple(patterns)


def disabled_route_patterns() -> tuple:
    """禁用模块的请求路径正则（空元组 = 无裁剪，调用方走零开销旁路）。"""

    return _disabled_route_regexes()


@lru_cache(maxsize=1)
def _disabled_ws_regexes() -> tuple:
    patterns = []
    for spec in _disabled_specs():
        for prefix in spec.ws_routes:
            patterns.append(re.compile(prefix))
    return tuple(patterns)


def disabled_ws_patterns() -> tuple:
    """禁用模块的 WebSocket 路径正则（空元组 = 无裁剪，调用方走零开销旁路）。"""

    return _disabled_ws_regexes()


def is_ws_path_trimmed(path: str) -> bool:
    """WS 路径（ASGI ``scope["path"]``，含前导斜杠）是否命中停用模块通道。"""

    patterns = _disabled_ws_regexes()
    if not patterns:
        return False
    return any(pattern.match(path or "") for pattern in patterns)


class ModuleTrimWebsocketMiddleware:
    """停用模块的 WebSocket 通道准入（第六层裁剪，fail-closed）。

    与 HTTP 侧 ``ModuleGateMiddleware`` 同源：通道归属在 ``ModuleSpec.ws_routes``
    单点声明，停用模块的通道在认证与 consumer 之前直接拒绝（close 4404，语义=
    通道不存在），避免「页面与 REST 已隐藏、WS 仍可连」的半残状态。

    - 未配置停用模块时零开销直通（不发散任何正则匹配）；
    - 内核通道（``ws/message``、``ws/tasks/log``）不声明即不拦截。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "websocket" and is_ws_path_trimmed(scope.get("path", "")):
            logger.warning("websocket rejected by module trim: %s", scope.get("path"))
            await send({"type": "websocket.close", "code": 4404})
            return
        return await self.app(scope, receive, send)


def permission_prefixes_of(specs) -> tuple:
    """给定模块集合的权限点 path 前缀。

    两条来源合并：
    1. ``ModuleSpec.permissions``：菜单树覆盖不到的权限点（如全局搜索）；
    2. ``ModuleSpec.routes``：路由前缀去掉 ``^`` 与前导 ``/`` 后的形态
       （权限点 path 形如 ``api/chat/room$``），保证权限码隐藏不依赖菜单层级
       ——管理员把权限点挂到别的菜单下也不会漏。
    """

    prefixes = []
    for spec in specs:
        prefixes.extend(spec.permissions)
        for route in spec.routes:
            path = route.lstrip("^").lstrip("/")
            if path:
                prefixes.append(path)
    return tuple(prefixes)


def disabled_permission_prefixes() -> tuple:
    """当前停用模块的权限点 path 前缀。"""

    return permission_prefixes_of(_disabled_specs())


@lru_cache(maxsize=1)
def _disabled_menu_pks_uncached() -> frozenset:
    if not _disabled_specs():
        return frozenset()
    try:
        from system.services import Menu

        rows = list(Menu.objects.values_list("pk", "parent_id", "menu_type", "name", "path"))
    except Exception as exc:  # noqa: BLE001 迁移期/空库等场景下裁剪退化为不裁剪
        logger.warning("module menu filter skipped: %s", exc)
        return frozenset()

    hidden = compute_hidden_menu_pks(rows)
    if hidden:
        logger.info("module menu filter: %s menus hidden", len(hidden))
    return hidden


def _disabled_menu_pks() -> frozenset:
    return _disabled_menu_pks_uncached()


def compute_hidden_menu_pks(rows, names=None, prefixes=None) -> frozenset:
    """计算需隐藏的菜单主键（纯函数：运行期过滤与种子导入共用同一口径）。

    规则：

    1. ``names`` 指定的菜单根 → 其整棵子树（含权限点行）；
    2. 权限点行 ``path`` 命中 ``prefixes`` → 该行本身（不依赖菜单层级，防止被改挂）；
    3. 子节点被全部隐藏的目录 → 目录本身（避免前端出现空分组）。

    :param rows: 可迭代的 ``(pk, parent_id, menu_type, name, path)``
    :param names: 需隐藏的菜单根 name（默认取停用模块声明）
    :param prefixes: 需隐藏的权限点 path 前缀（默认取停用模块声明）
    """

    names = {name for spec in _disabled_specs() for name in spec.menus} if names is None else set(names)
    prefixes = disabled_permission_prefixes() if prefixes is None else tuple(prefixes)
    if not names and not prefixes:
        return frozenset()

    # 入参可能是生成器（种子侧直接传推导式），必须物化：rows 会被遍历两次
    rows = list(rows)
    children: dict = {}
    by_name: dict = {}
    hidden: set = set()
    # _prefix_regex 返回的是给 path__regex 用的模式串，这里需要编译后再自行匹配
    permission_re = re.compile(_prefix_regex(prefixes)) if prefixes else None
    for pk, parent_id, menu_type, name, path in rows:
        children.setdefault(parent_id, []).append(pk)
        by_name.setdefault(name, []).append(pk)
        if permission_re is not None and menu_type == MENU_TYPE_PERMISSION and path and permission_re.match(path):
            hidden.add(pk)

    hidden.update(pk for name in names for pk in by_name.get(name, ()))
    stack = list(hidden)
    while stack:
        for child in children.get(stack.pop(), ()):
            if child not in hidden:
                hidden.add(child)
                stack.append(child)

    directories = [(pk, parent_id) for pk, parent_id, menu_type, _n, _p in rows if menu_type == MENU_TYPE_DIRECTORY]
    changed = True
    while changed:
        changed = False
        for pk, _parent_id in directories:
            if pk in hidden:
                continue
            kids = children.get(pk) or []
            if kids and all(child in hidden for child in kids):
                hidden.add(pk)
                changed = True
    return frozenset(hidden)


def filter_menu_queryset(queryset):
    """剔除禁用模块的菜单行（含权限点行）；无禁用模块时原样返回。"""

    resolution = resolve_modules()
    if resolution.is_full:
        return queryset
    hidden = _disabled_menu_pks()
    prefixes = disabled_permission_prefixes()
    if not hidden and not prefixes:
        return queryset
    try:
        from system.services import Menu

        # 显式逐条 OR（不使用空 Q 起步：空 Q 参与 OR 的语义在 SQL 层不稳妥）
        conditions = []
        if hidden:
            conditions.append(Q(pk__in=tuple(hidden)))
        if prefixes:
            conditions.append(Q(menu_type=Menu.MenuChoices.PERMISSION, path__regex=_prefix_regex(prefixes)))
        condition = conditions[0]
        for extra in conditions[1:]:
            condition |= extra
        return queryset.exclude(condition)
    except Exception as exc:  # noqa: BLE001 依赖异常时不隐藏（仅记录），避免影响主链路
        logger.warning("module menu filter skipped: %s", exc)
        return queryset


@lru_cache(maxsize=8)
def _prefix_regex(prefixes: tuple) -> str:
    return "^(" + "|".join(re.escape(prefix) for prefix in prefixes) + ")"


def invalidate_trimmed_caches() -> int:
    """清理受模块裁剪影响的缓存（进程启动时调用一次）。

    菜单路由与用户权限码缓存 TTL 均为 24 小时，而模块组合只在 config.yml 变更
    并重启后生效：启动时清理一次，避免「模块已关停、菜单仍显示 24 小时」。
    未配置停用模块（preset=full 且无覆盖）时直接返回 0，无任何开销。
    """

    if resolve_modules().is_full:
        return 0
    from django.core.cache import cache

    removed = 0
    for pattern in (
        "magic_cache_data_get_user_permission*",
        "magic_cache_response_UserRoutesAPIView*",
    ):
        try:
            removed += cache.delete_pattern(pattern) or 0
        except Exception as exc:  # noqa: BLE001 缓存异常不影响启动
            logger.warning("module trim invalidate %s failed: %s", pattern, exc)
    logger.info("module trim: invalidated %s cached entries", removed)
    return removed
