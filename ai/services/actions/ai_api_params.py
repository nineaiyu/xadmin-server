#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""声明式 AI API 动作：参数解析与 URL 构建（自 ai_api_actions 拆分，行为不变）。

``ai_api_actions.ApiActionSpec`` 的 validate / execute 复用本模块：类型转换
（user / role / menu / pk / bool / int / enum / json）、path 占位展开与 URL 反解
前置校验。

展示值与执行值的两段式解析：确认卡片展示可读名称（昵称/角色名/菜单名），执行期
再解析回主键。validate 时把「展示文本 → 已解析主键」记入短 TTL 缓存，执行期优先
命中该映射——改名/重名不会让已确认草稿解析失败或误解析到另一个对象；缓存过期或
对象已删除时回落名称解析（仍返回可读错误而不是误操作）。
"""

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any

from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

if TYPE_CHECKING:
    from ai.services.actions.ai_api_actions import ApiActionSpec

logger = get_logger(__name__)

#: 展示值解析缓存前缀（validate → execute 窗口内保持「确认过的对象」不变）
PARAM_RESOLUTION_CACHE_PREFIX = "ai_api_params_resolve_"
#: 解析缓存 TTL（秒）：覆盖用户确认草稿的常规决策窗口
PARAM_RESOLUTION_TTL = 600

#: 参数归属：path（填 URL 占位）/ body（请求体字段）/ query（URL 查询串，GET 列表类动作）
IN_PATH = "path"
IN_BODY = "body"
IN_QUERY = "query"

#: Django path 占位（``<pk>``）→ 菜单权限点 path 正则（``(?P<pk>[^/.]+)``）的匹配约定：
#: 占位值不含 ``/`` 与 ``.``，因此模板字面量可直接命中权限点正则（见 required_visits）。
PATH_PLACEHOLDER = re.compile(r"<([a-zA-Z_][a-zA-Z0-9_]*)>")

_TRUE_WORDS = ("true", "1", "yes", "y", "是", "启用", "enable", "enabled")
_FALSE_WORDS = ("false", "0", "no", "n", "否", "禁用", "disable", "disabled")


class ApiActionError(DjangoValidationError):
    """动作声明/参数解析期的可读错误（转前端文案）。"""


def _resolve_user(value: Any) -> Any:
    """用户名/昵称/``昵称(用户名)``/主键 → 用户主键（不存在则报可读错误）。

    宽容解析保证两段式幂等：``validate`` 输出的可读展示值（``昵称(用户名)``）与
    前端回传的主键都能在 ``execute`` 阶段解析回同一个用户。
    """
    from identity.contracts import resolve_user

    text = str(value or "").strip()
    if not text:
        raise ApiActionError(_("A user must be specified"))
    if text.endswith(")") and "(" in text:
        text = text.rsplit("(", 1)[-1].rstrip(")")
    user = resolve_user(text)
    if user is None:
        raise ApiActionError(_("No such user: {}").format(text))
    return str(user.pk)


def _user_display(pk: str) -> str:
    """用户主键 → 确认卡片展示文本 ``昵称(用户名)``（昵称缺失则仅用户名）。

    与内核显示名 :func:`common.utils.display.user_label`（单值口径）刻意不同：
    确认卡片需要同时呈现昵称与账号，避免重名用户无法区分。
    """
    from identity.contracts import get_user

    user = get_user(pk)
    if user is None:
        return str(pk)
    nickname = (user.nickname or "").strip()
    return f"{nickname}({user.username})" if nickname else str(user.username)


def _resolve_role(value: Any) -> Any:
    """角色名/主键 → 角色主键（确认卡片回显用展示文本，与 user 同口径）。"""
    from identity.contracts import resolve_role

    text = str(value or "").strip()
    if not text:
        raise ApiActionError(_("A role must be specified"))
    role = resolve_role(text)
    if role is None:
        raise ApiActionError(_("No such role: {}").format(text))
    return str(role.pk)


def _role_display(pk: str) -> str:
    """角色主键 → 确认卡片展示文本（角色名）。"""
    from identity.contracts import get_role

    role = get_role(pk)
    return role.name if role else str(pk)


def _menu_display(pks: Any) -> list[Any]:
    """菜单主键列表 → 名称数组（确认卡片展示；回传执行期按名称再解析，语义幂等）。"""
    from system.models import Menu

    if not isinstance(pks, list):
        typed_value: list[Any] = pks
        return typed_value
    names = []
    for pk in pks:
        menu = Menu.objects.filter(pk=pk).first()
        names.append(menu.name if menu else str(pk))
    return names


def _resolution_cache_key(kind: str, display: Any) -> str:
    """展示值 → 解析结果的缓存键（展示值可为标量或名称数组，统一 JSON 归一）。"""
    payload = json.dumps(display, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(f"{kind}:{payload}".encode()).hexdigest()[:32]
    return f"{PARAM_RESOLUTION_CACHE_PREFIX}{kind}_{digest}"


def remember_param_resolution(kind: str, display: Any, resolved: Any) -> None:
    """记录 validate 阶段确认的「展示值 → 主键」，供 execute 阶段稳定解析。"""
    try:
        cache.set(_resolution_cache_key(kind, display), resolved, PARAM_RESOLUTION_TTL)
    except Exception:  # noqa: BLE001 缓存不可用时执行期回落名称解析
        logger.debug("cache AI action param resolution failed", exc_info=True)


def _recall_param_resolution(kind: str, value: Any) -> Any:
    try:
        return cache.get(_resolution_cache_key(kind, value))
    except Exception:  # noqa: BLE001
        return None


def _cached_resolution_valid(kind: str, cached: Any) -> bool:
    """缓存的主键仍存在（对象删除后回落名称解析，不静默操作已删除对象）。"""
    if kind == "user":
        from identity.contracts import get_user

        return get_user(cached) is not None
    if kind == "role":
        from identity.contracts import get_role

        return get_role(cached) is not None
    if kind == "menu":
        from system.models import Menu

        pks = cached if isinstance(cached, (list, tuple)) else []
        return bool(pks) and Menu.objects.filter(pk__in=pks, deleted_at__isnull=True).count() == len(pks)
    return False


def _menu_subtree_pks(menu: Any) -> list[Any]:
    """菜单子树主键（含自身与全部后代）：授权语义与 UI 勾选父节点（全选子级）一致。"""
    from system.models import Menu

    pks = [str(menu.pk)]
    frontier = [menu.pk]
    while frontier:
        children = list(Menu.objects.filter(parent_id__in=frontier).values_list("pk", flat=True))
        frontier = [pk for pk in children if str(pk) not in pks]
        pks.extend(str(pk) for pk in children)
    return pks


def _resolve_menu(value: Any) -> Any:
    """菜单名/主键 → 该菜单及其子树全部主键（数组值逐项解析后去重）。"""
    from system.models import Menu

    if isinstance(value, (list, tuple)):
        merged: list[Any] = []
        for item in value:
            merged.extend(_resolve_menu(item))
        return list(dict.fromkeys(merged))
    text = str(value or "").strip()
    if not text:
        raise ApiActionError(_("A menu must be specified"))
    menu = None
    if re.fullmatch(r"[0-9a-fA-F-]{32,36}", text):
        menu = Menu.objects.filter(pk=text).first()
    if menu is None:
        matches = list(Menu.objects.filter(name=text))
        if len(matches) > 1:
            raise ApiActionError(_("Multiple menus named {} exist; use the primary key").format(text))
        menu = matches[0] if matches else None
    if menu is None:
        raise ApiActionError(_("No such menu: {}").format(text))
    return _menu_subtree_pks(menu)


def _convert(kind: str, value: Any, rule: dict[str, Any], field: str) -> Any:
    """按声明类型转换单个参数值（失败抛 ApiActionError）。

    user / role / menu 先命中 validate 阶段登记的展示值映射：确认后对象被改名或
    出现同名对象时，仍按「确认时的对象」执行，而不是解析失败/误解析。
    """
    if kind in ("user", "role", "menu"):
        cached = _recall_param_resolution(kind, value)
        if cached is not None and _cached_resolution_valid(kind, cached):
            return list(cached) if kind == "menu" else str(cached)
    if kind == "user":
        return _resolve_user(value)
    if kind == "role":
        return _resolve_role(value)
    if kind == "menu":
        return _resolve_menu(value)
    if kind == "pk":
        return str(value)
    if kind == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
        raise ApiActionError(_("Parameter {} must be a boolean").format(field))
    if kind == "int":
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ApiActionError(_("Parameter {} must be a number").format(field)) from exc
    if kind == "enum":
        text = str(value).strip()
        allowed = [str(item) for item in rule.get("values") or []]
        if text not in allowed:
            raise ApiActionError(_("Parameter {} must be one of: {}").format(field, ", ".join(allowed)))
        return text
    if kind == "json":
        if not isinstance(value, (dict, list)):
            raise ApiActionError(_("Parameter {} must be a JSON object or array").format(field))
        return value
    return str(value)


def resolve_api_params(spec: "ApiActionSpec", user: Any, params: dict[str, Any]) -> Any:
    """参数解析：返回 (path 参数 dict, 请求体 dict, query dict, 错误文案)。"""
    raw = params if isinstance(params, dict) else {}
    path_params: dict[str, Any] = {}
    body: dict[str, Any] = {}
    query: dict[str, Any] = {}
    for field, rule in spec.params.items():
        if "const" in rule:
            # 服务端固定值（如公告 notice_type / 空接收人列表）：原样注入，
            # 不做类型转换（值已是接口契约要求的形态，也不来自模型）
            resolved = rule["const"]
        else:
            value = raw.get(field, rule.get("default"))
            if value is None or (isinstance(value, str) and not value.strip()):
                if rule.get("required"):
                    return None, None, None, str(_("Missing required parameter: {}").format(field))
                continue
            try:
                resolved = _convert(str(rule.get("type") or "string"), value, rule, field)
            except ApiActionError as exc:
                return None, None, None, "; ".join(str(item) for item in exc.messages)
        target_key = rule.get("target", field)
        where = rule.get("in", IN_BODY)
        if where == IN_PATH:
            # path 占位按声明参数名替换（build_action_url 按声明名展开）
            path_params[field] = resolved
        elif where == IN_QUERY:
            query[target_key] = resolved
        else:
            body[target_key] = resolved
    return path_params, body, query, None


def build_action_url(spec: "ApiActionSpec", path_params: dict[str, Any]) -> Any:
    """URL 模板 + path 参数 → 可解析的真实路径（占位缺失返回 None）。"""
    url = spec.path
    for name, value in path_params.items():
        url = url.replace(f"<{name}>", str(value))
    return None if PATH_PLACEHOLDER.search(url) else url
