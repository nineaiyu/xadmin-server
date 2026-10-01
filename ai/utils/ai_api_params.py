#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""声明式 AI API 动作：参数解析与 URL 构建（自 ai_api_actions 拆分，行为不变）。

``ai_api_actions.ApiActionSpec`` 的 validate / execute 复用本模块：类型转换
（user / role / menu / pk / bool / int / enum / json）、path 占位展开与 URL 反解
前置校验。
"""

import re
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    from ai.utils.ai_api_actions import ApiActionSpec

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


def _resolve_user(value):
    """用户名/昵称/``昵称(用户名)``/主键 → 用户主键（不存在则报可读错误）。

    宽容解析保证两段式幂等：``validate`` 输出的可读展示值（``昵称(用户名)``）与
    前端回传的主键都能在 ``execute`` 阶段解析回同一个用户。
    """
    from system.models import UserInfo

    text = str(value or "").strip()
    if not text:
        raise ApiActionError(_("A user must be specified"))
    if text.endswith(")") and "(" in text:
        text = text.rsplit("(", 1)[-1].rstrip(")")
    user = UserInfo.objects.filter(username=text).first() or UserInfo.objects.filter(nickname=text).first()
    if user is None:
        try:
            user = UserInfo.objects.filter(pk=text).first()
        except (DjangoValidationError, ValueError, TypeError):
            user = None
    if user is None:
        raise ApiActionError(_("No such user: {}").format(text))
    return str(user.pk)


def _user_display(pk: str) -> str:
    """用户主键 → 确认卡片展示文本 ``昵称(用户名)``（昵称缺失则仅用户名）。"""
    from system.models import UserInfo

    user = UserInfo.objects.filter(pk=pk).first()
    if user is None:
        return str(pk)
    nickname = (user.nickname or "").strip()
    return f"{nickname}({user.username})" if nickname else str(user.username)


def _resolve_role(value):
    """角色名/主键 → 角色主键（确认卡片回显用展示文本，与 user 同口径）。"""
    from system.models import UserRole

    text = str(value or "").strip()
    if not text:
        raise ApiActionError(_("A role must be specified"))
    role = None
    if re.fullmatch(r"[0-9a-fA-F-]{32,36}", text):
        role = UserRole.objects.filter(pk=text).first()
    if role is None:
        role = UserRole.objects.filter(name=text).first()
    if role is None:
        raise ApiActionError(_("No such role: {}").format(text))
    return str(role.pk)


def _role_display(pk: str) -> str:
    """角色主键 → 确认卡片展示文本（角色名）。"""
    from system.models import UserRole

    role = UserRole.objects.filter(pk=pk).first()
    return role.name if role else str(pk)


def _menu_display(pks) -> list:
    """菜单主键列表 → 名称数组（确认卡片展示；回传执行期按名称再解析，语义幂等）。"""
    from system.models import Menu

    if not isinstance(pks, list):
        return pks
    names = []
    for pk in pks:
        menu = Menu.objects.filter(pk=pk).first()
        names.append(menu.name if menu else str(pk))
    return names


def _menu_subtree_pks(menu) -> list:
    """菜单子树主键（含自身与全部后代）：授权语义与 UI 勾选父节点（全选子级）一致。"""
    from system.models import Menu

    pks = [str(menu.pk)]
    frontier = [menu.pk]
    while frontier:
        children = list(Menu.objects.filter(parent_id__in=frontier).values_list("pk", flat=True))
        frontier = [pk for pk in children if str(pk) not in pks]
        pks.extend(str(pk) for pk in children)
    return pks


def _resolve_menu(value):
    """菜单名/主键 → 该菜单及其子树全部主键（数组值逐项解析后去重）。"""
    from system.models import Menu

    if isinstance(value, (list, tuple)):
        merged: list = []
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


def _convert(kind: str, value, rule: dict, field: str):
    """按声明类型转换单个参数值（失败抛 ApiActionError）。"""
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


def resolve_api_params(spec: "ApiActionSpec", user, params: dict):
    """参数解析：返回 (path 参数 dict, 请求体 dict, query dict, 错误文案)。"""
    raw = params if isinstance(params, dict) else {}
    path_params: dict = {}
    body: dict = {}
    query: dict = {}
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


def build_action_url(spec: "ApiActionSpec", path_params: dict):
    """URL 模板 + path 参数 → 可解析的真实路径（占位缺失返回 None）。"""
    url = spec.path
    for name, value in path_params.items():
        url = url.replace(f"<{name}>", str(value))
    return None if PATH_PLACEHOLDER.search(url) else url
