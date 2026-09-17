#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""菜单权限点同步内核：路由扫描与缺口规划。"""

import re
from collections import Counter

from django.conf import settings

from common.core.utils import get_all_url_dict
from common.utils import get_logger
from system.models import Menu, ModelLabelField
from system.utils.menu import get_view_permissions

from .constants import DEAD_ENDPOINT_PREFIXES, PARENT_MENU_MAP, SHARED_METHOD_PATHS, SKIP_ROUTE_PREFIXES
from .types import PlanItem, RouteInfo

logger = get_logger(__name__)


def sample_path(path: str) -> str:
    """把 URL 正则样例化为可 resolve 的请求路径（与权限判定脚本同口径）。"""
    s = path.replace("[^/.]+", "1").replace("[^/]+", "1").replace("\\d+", "1").replace(".*", "x")
    s = re.sub(r"\(\?P<[^>]+>", "", s)
    return s.replace("(?:", "").replace(")", "").replace("?", "")


def url_to_sample(url: str) -> str:
    return sample_path("/" + url.rstrip("$"))


def path_whitelisted(sample: str, method: str) -> bool:
    return any(
        re.match(w_url, sample) and ("*" in methods or method in methods)
        for w_url, methods in settings.PERMISSION_WHITE_URL.items()
    )


def requires_permission(view_cls) -> bool:
    """视图是否走默认菜单权限链（显式 AllowAny / 空清单 / 自定义权限类不走）。"""
    permission_classes = getattr(view_cls, "permission_classes", None)
    if permission_classes is None:
        return True
    names = [getattr(item, "__name__", "") for item in permission_classes]
    return bool(permission_classes) and "IsAuthenticated" in names and "AllowAny" not in names


def ensure_urlconf_loaded():
    """确保 URLconf 模块已导入（get_all_url_dict 内部走 import_string(ROOT_URLCONF)，
    命令行/测试进程未加载 URLconf 时会 ImportError）。"""
    from django.utils.module_loading import import_module

    import_module(settings.ROOT_URLCONF)


def build_route_index():
    """扫描全量路由（仅 api/ 前缀），解析出视图、方法与是否需要权限点。"""
    from django.urls import resolve
    from django.urls.exceptions import Resolver404

    ensure_urlconf_loaded()

    routes = []
    for item in get_all_url_dict(""):
        url = str(item.get("url") or "")
        if not url.startswith("api/"):
            continue
        if url.startswith(SKIP_ROUTE_PREFIXES):
            continue
        sample = url_to_sample(url)
        resolved = None
        for candidate in (sample, f"{sample}/"):
            try:
                resolved = resolve(candidate)
                break
            except Resolver404:
                continue
        if resolved is None:
            logger.warning(f"route resolve failed, skip: {url}")
            continue
        routes.append(
            RouteInfo(
                view=str(item.get("view")),
                name=str(item.get("name")),
                url=url,
                sample=sample,
                actions=dict(getattr(resolved.func, "actions", {}) or {}),
                view_cls=getattr(resolved.func, "cls", None),
                requires_permission=requires_permission(getattr(resolved.func, "cls", None)),
            )
        )
    return routes


def load_permission_menus():
    return list(Menu.objects.filter(menu_type=Menu.MenuChoices.PERMISSION, deleted_at__isnull=True))


def _method_covers(perm, path, method):
    """方法匹配：同方法直接命中；登记的多方法共享端点按 SHARED_METHOD_PATHS 放宽。"""
    perm_method = (perm.method or "").upper()
    if perm_method == method:
        return True
    shared = SHARED_METHOD_PATHS.get(f"{path}$") or ()
    return method in shared and perm_method in shared


def find_covering(perms, path, method):
    """与运行时 get_menu_pk 同口径：精确 `path$` 优先，其次正则前缀回退。"""
    exact = f"{path}$"
    for perm in perms:
        if perm.path == exact and _method_covers(perm, path, method):
            return perm
    target = "/" + path
    for perm in perms:
        if not _method_covers(perm, path, method):
            continue
        try:
            if re.match("/" + perm.path, target):
                return perm
        except re.error:
            continue
    return None


def scan_gaps(routes, perms):
    """扫描需要权限点但未覆盖的端点：返回 [(RouteInfo, METHOD, action)]。"""
    gaps = []
    for route in routes:
        if not route.requires_permission or route.url.startswith(DEAD_ENDPOINT_PREFIXES):
            continue
        path = route.url.rstrip("$")
        for method, action in route.actions.items():
            upper = method.upper()
            if upper == "PUT":  # 生成器设计：ViewSet 忽略 PUT（前端统一用 PATCH）
                continue
            if path_whitelisted(route.sample, upper):
                continue
            if find_covering(perms, path, upper):
                continue
            gaps.append((route, upper, action))
    return gaps


def resolve_view_context(view, view_route_urls, perms, default_parent=None):
    """解析某视图的权限码后缀与父菜单（返回 (suffix, parent, source)）。

    优先复用同视图既有权限点（后缀/父菜单），保证与 UI 生成结果一致；
    无同源权限点时按 PARENT_MENU_MAP 前缀映射，最后回退 default_parent。
    """
    suffixes, parents = [], []
    for perm in perms:
        if perm.path not in view_route_urls and f"{perm.path}$" not in view_route_urls:
            continue
        if ":" in perm.name:
            suffixes.append(perm.name.split(":", 1)[1])
        if perm.parent_id:
            parents.append(perm.parent)
    if suffixes:
        suffix = Counter(suffixes).most_common(1)[0][0]
        parent = Counter(parents).most_common(1)[0][0] if parents else None
        return suffix, parent, "existing"

    for prefix, menu_name in PARENT_MENU_MAP.items():
        if not any(url.startswith(prefix) for url in view_route_urls):
            continue
        parent = Menu.objects.filter(name=menu_name, menu_type=Menu.MenuChoices.MENU, deleted_at__isnull=True).first()
        if parent:
            return parent.name, parent, "prefix-map"

    if default_parent:
        return default_parent.name, default_parent, "default"
    return None, None, "unresolved"


def build_plans(gaps, routes, perms, default_parent=None):
    """把缺口规划为待创建的权限点（不落库）。返回 (plans, unresolved)。"""
    by_view = {}
    for route, method, action in gaps:
        by_view.setdefault(route.view, []).append((route, method, action))

    ensure_urlconf_loaded()
    plans, unresolved = [], []
    for view, items in sorted(by_view.items()):
        all_urls = {r.url for r in routes if r.view == view}
        suffix, parent, source = resolve_view_context(view, all_urls, perms, default_parent)
        if not suffix:
            unresolved.extend(items)
            continue

        canonical = {}
        for entry in get_view_permissions(view, suffix):
            canonical[(entry["url"], str(entry["method"]).upper())] = entry

        for index, (route, method, action) in enumerate(sorted(items, key=lambda x: (x[0].url, x[1]))):
            entry = canonical.get((route.url, method))
            if entry:
                code = entry["code"]
                description = entry["description"] or view
                labels = list(entry.get("models") or [])
                plan_source = "generator"
            else:
                # 兜底：与 get_view_permissions 同规则的 code（视图未产出该路由时）
                code_part = action.title().replace("_", "").replace("-", "")
                code = f"{code_part[0].lower()}{code_part[1:]}:{suffix}"
                description = view
                labels = []
                plan_source = "fallback"

            model_pks = list(
                ModelLabelField.objects.filter(
                    field_type=ModelLabelField.FieldChoices.ROLE, parent=None, name__in=labels
                ).values_list("pk", flat=True)
            )
            plans.append(
                PlanItem(
                    view=view,
                    url=route.url,
                    method=method,
                    action=action,
                    code=code,
                    description=description,
                    parent_id=parent.pk if parent else None,
                    parent_name=parent.name if parent else "",
                    model_pks=model_pks,
                    rank=10000 + index,
                    source=plan_source,
                )
            )
    return plans, unresolved
