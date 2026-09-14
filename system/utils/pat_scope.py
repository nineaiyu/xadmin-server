#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""PAT scope 选项：把「当前用户有权限的接口」整理成可勾选的分组清单。

数据源与请求鉴权同源——权限菜单（`menu_type=PERMISSION`）× 用户角色
（`get_user_permission`，按 HTTP 方法维度），因此「勾选项」与「该用户能调什么」
天然一致，不需要用户自己猜路径：

- 超管走全部启用的权限菜单：`IsAuthenticated` 对超管直接放行（不查角色），
  其可授权范围不应受「有没有绑定角色」影响；
- 菜单 path 是 DRF 路由片段（含 `(?P<pk>[^/.]+)` 占位与尾部 `$`），转成**锚定**
  scope 条目（`GET ^/api/system/user/?$`）写入：精确到这一个接口，不会连带放行
  同前缀的兄弟接口（如 `.../cancel`）或相似前缀（如 `/api/system/user-center`）。

展示字段（label / display_path / method）与提交字段（value = scope 条目本体）分离：
value 一旦入库即为 scope 语义（正则），前端只负责搬运与勾选。
"""

from __future__ import annotations

import re

from common.core.permission import get_user_permission
from common.utils import get_logger
from system.models import Menu

logger = get_logger(__name__)

# 可收口的 HTTP 方法（与 common.core.auth.SCOPE_HTTP_METHODS 同口径的业务子集）
SCOPE_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
_METHOD_ORDER = {method: index for index, method in enumerate(SCOPE_METHODS)}

# 菜单 path 中的 DRF 路由占位符：`(?P<pk>[^/.]+)`
_PLACEHOLDER_RE = re.compile(r"\(\?P<(?P<name>[^>]+)>[^)]*\)")
# 菜单标题录入习惯的操作标记（C-/U-/L-/R-/D- 等），仅用于展示美化
_TITLE_TAG_RE = re.compile(r"^[A-Za-z]-\s*")


def scope_entry(method: str, path: str) -> str:
    """权限菜单 path → 单个接口的锚定 scope 条目。

    ```
    api/system/user$                        → GET ^/api/system/user/?$
    api/system/user/(?P<pk>[^/.]+)$         → GET ^/api/system/user/[^/]+/?$
    api/system/user/(?P<pk>[^/.]+)/cancel$  → GET ^/api/system/user/[^/]+/cancel/?$
    ```

    条目路径部分是 Python 正则（scope 判定用 `re.search`）：

    - `^…$` 锚定：避免 `/api/system/user` 连带放行 `/api/system/user-center`；
    - `/?` 兼容带尾斜杠的请求地址；
    - 占位符退化为单段通配 `[^/]+`：命名组在 scope 里无意义，可读性也差。
    """
    body = str(path or "").strip().rstrip("$").strip()
    if not body:
        return ""
    body = _PLACEHOLDER_RE.sub("[^/]+", body).strip("/")
    if not body:
        return ""
    return f"{method} ^/{body}/?$"


def scope_display_path(path: str) -> str:
    """菜单 path → 人可读路径（占位符保留为 ``{pk}`` 形态，供界面展示）。"""
    body = str(path or "").strip().rstrip("$").strip().lstrip("/")
    return "/" + _PLACEHOLDER_RE.sub(lambda match: "{{{}}}".format(match.group("name")), body)


def _menu_title(menu) -> str:
    """菜单标题（原样返回：可能是 i18n key，由前端 te() 翻译）。"""
    if menu is None:
        return ""
    try:
        return (menu.meta.title or "").strip()
    except Exception:  # noqa: BLE001 meta 缺失/已删时按无标题处理
        return ""


def _option_label(menu) -> str:
    """权限项展示名：去操作标记的菜单标题 → 权限码资源段 → 权限码。"""
    title = _TITLE_TAG_RE.sub("", _menu_title(menu))
    if title:
        return title
    code = (menu.name or "").strip()
    return code.split(":", 1)[-1] or code


def _iter_scope_menus(user):
    """产出 ``(method, Menu)``：该用户有权限的接口菜单（超管为全部启用权限菜单）。"""
    if user.is_superuser:
        queryset = Menu.objects.filter(menu_type=Menu.MenuChoices.PERMISSION, is_active=True).select_related(
            "meta", "parent", "parent__meta"
        )
        for menu in queryset:
            method = (menu.method or "").upper()
            if method in _METHOD_ORDER:
                yield method, menu
        return

    for method in SCOPE_METHODS:
        try:
            permission_data = get_user_permission(user, method)
        except Exception as e:  # noqa: BLE001 权限查询失败按无该方法的可选项处理
            logger.warning(f"list pat scope menus failed. user:{user} method:{method} error:{e}")
            continue
        # permission_data: {path: (menu_pk, model)}，只取 pk 回查菜单（标题/分组展示用）
        pks = [item[0] for item in permission_data.values() if item and item[0]]
        if not pks:
            continue
        queryset = Menu.objects.filter(pk__in=pks, is_active=True).select_related("meta", "parent", "parent__meta")
        for menu in queryset:
            yield (menu.method or method).upper(), menu


def scope_options_for_user(user) -> dict:
    """当前用户可授权的接口范围，按父菜单分组（供令牌接口范围勾选）。"""
    groups: dict[str, dict] = {}
    seen = set()
    total = 0
    for method, menu in _iter_scope_menus(user):
        value = scope_entry(method, menu.path)
        if not value or value in seen:
            continue
        seen.add(value)
        parent = menu.parent
        group_key = str(parent.pk) if parent is not None else "root"
        group = groups.setdefault(group_key, {"key": group_key, "title": _menu_title(parent), "options": []})
        group["options"].append(
            {
                "value": value,
                "method": method,
                "path": scope_display_path(menu.path),
                "label": _option_label(menu),
                "code": menu.name or "",
            }
        )
        total += 1

    ordered = []
    for group in groups.values():
        group["options"].sort(key=lambda item: (_METHOD_ORDER.get(item["method"], 99), item["path"]))
        ordered.append(group)
    ordered.sort(key=lambda item: (item["title"] or "", item["key"]))
    return {"total": total, "groups": ordered}
