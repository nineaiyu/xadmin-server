#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""声明式 AI API 动作：复用现有业务接口执行（零 AI 专用业务代码）。

**为什么这样设计（重要）**

受限动作第一版为每个能力手写「校验 + 执行」函数（请假、动态表单），代价是：
新增能力（如"禁用某用户"）必须在 AI 侧再实现一遍业务规则，与既有
ViewSet / Serializer 双写，必然漂移。这里改为**声明式复用业务接口**：

- 新增能力 = 在 ``API_ACTION_SPECS`` 里声明 (method, path, 参数类型)，零业务代码；
- 执行 = 构造 DRF 请求 → ``resolve()`` 反解到现有 ViewSet 的 view 函数 → 直接
  dispatch——权限链（含菜单权限点）、序列化器校验、写入事务、缓存失效、操作日志
  与浏览器请求**完全同一代码路径**，不存在"AI 专用分支"；
- 安全：只能命中白名单声明的 (method, path)；path 模板由服务端持有，LLM 只填
  参数值；执行前 ``user_can_visit`` 预检 + 视图自带 permission_classes 双门；
  仍需用户确认卡片二次确认，并落 AI:action 审计（与既有动作同口径）。

协议：本模块的 ``ApiActionSpec`` 与 ``ai_actions.ActionSpec`` 同形状
（key/label/description/params/required_visits/has_permission/validate/execute/
requires_approval/available），可直接混装进同一注册表，调用方无需区分两类动作。
"""

import json
import re
from dataclasses import dataclass

from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import Resolver404, resolve
from django.utils.translation import gettext_lazy as _
from rest_framework.request import Request  # noqa: F401  （类型语义保留：dispatch 接收 DRF 请求）
from rest_framework.test import APIRequestFactory

from common.utils import get_logger

logger = get_logger(__name__)

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


def requires_approval_high_risk(user, params) -> bool:
    """高危动作共享谓词：非超管一律进 412 审批协议（审批单 module=AI 动作）。

    超管豁免与 dform 动作同口径：审批协议要求「申请人不能自审」，而超管通常
    是唯一管理员——若超管也强制审批，动作会因审批人缺失/自审禁止而永久挂起。
    """
    return not getattr(user, "is_superuser", False)


@dataclass(frozen=True)
class ApiActionSpec:
    """声明式 API 动作（协议对齐 ai_actions.ActionSpec，可混装注册表）。"""

    key: str
    label: object
    description: object
    method: str
    path: str
    params: dict
    requires_approval: object
    available: object

    @property
    def required_visits(self) -> tuple:
        """执行所需业务权限点（method + path 模板，与菜单权限点 path 同口径）。"""
        return ((self.method.upper(), self.path),)

    def has_permission(self, user) -> bool:
        """与 ActionSpec 同口径双门：业务权限点 + 可用性。"""
        from ai.utils.ai_actions import user_can_visit

        return all(user_can_visit(user, method, path) for method, path in self.required_visits) and bool(
            self.available(user)
        )

    def validate(self, user, params):
        """参数解析（类型转换 + 必填/枚举校验）。

        契约与 ``ai_actions.ActionSpec.validate`` 一致：返回 ``(扁平参数, 错误文案)``。
        扁平参数用于「确认卡片展示 + 前端回传执行」；``const`` 固定值不出现在其中
        （执行期由声明重新注入）。解析是幂等的（已解析出的用户主键可再次通过校验），
        因此回传参数可直接执行。
        """
        path_params, body, query, error = resolve_api_params(self, user, params)
        if error:
            return {}, error
        clean = {}
        for field, rule in self.params.items():
            if "const" in rule:
                continue
            where = rule.get("in", IN_BODY)
            target_key = rule.get("target", field)
            if where == IN_PATH:
                clean[field] = path_params.get(field)
            elif where == IN_QUERY:
                clean[field] = query.get(target_key)
            else:
                clean[field] = body.get(target_key)
            # user/role 类型字段：确认卡片展示可读文本（昵称(用户名)/角色名），
            # 执行期宽容解析回主键（解析幂等）；menu 保持主键列表（执行期原样可解析）
            if rule.get("type") == "user" and clean.get(field):
                clean[field] = _user_display(clean[field])
            if rule.get("type") == "role" and clean.get(field):
                clean[field] = _role_display(clean[field])
            if rule.get("type") == "menu" and clean.get(field):
                clean[field] = _menu_display(clean[field])
        return clean, None

    def execute(self, user, params):
        """dispatch 到现有业务接口，返回 {ok, detail, data}。"""
        return execute_api_action(self, user, params)


def api_action(
    key: str,
    label,
    description,
    method: str,
    path: str,
    params: dict,
    defaults: dict = None,
    requires_approval=None,
    available=None,
) -> ApiActionSpec:
    """构造声明式动作。

    params 的每个字段：
    - ``type``：``user``（用户名/昵称 → 用户，解析出 pk 供 path 占位）/ ``pk`` /
      ``string`` / ``int`` / ``bool`` / ``enum``（配合 ``values``）/ ``json``；
    - ``in``：``body``（默认）或 ``path``（字段名须与 path 里的 ``<占位>`` 同名）；
    - ``target``：发送到业务接口时的字段名（缺省与参数名相同）——参数名可以
      对 LLM 友好（如 ``menus``），落到请求体时改写为接口契约字段（``menu``）；
    - ``required`` / ``default`` / ``description``（给 LLM 的说明）；
    - ``const``：服务端固定值（不进 LLM 目录、不接受模型提供，如公告 notice_type）。

    defaults：接口的隐含必填字段固定值（同样不进 LLM 目录），例如公告端点的
    ``notice_type/publish/notice_user/files``——业务接口的既有契约原样满足，
    仅声明不改写。
    """
    merged = dict(params)
    for name, value in (defaults or {}).items():
        merged.setdefault(name, {"const": value, "in": IN_BODY})
    return ApiActionSpec(
        key=key,
        label=label,
        description=description,
        method=method.upper(),
        path=path,
        params=merged,
        requires_approval=requires_approval or (lambda user, params: False),
        available=available or (lambda user: True),
    )


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


def resolve_api_params(spec: ApiActionSpec, user, params: dict):
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


def build_action_url(spec: ApiActionSpec, path_params: dict):
    """URL 模板 + path 参数 → 可解析的真实路径（占位缺失返回 None）。"""
    url = spec.path
    for name, value in path_params.items():
        url = url.replace(f"<{name}>", str(value))
    return None if PATH_PLACEHOLDER.search(url) else url


def execute_api_action(spec: ApiActionSpec, user, params: dict) -> dict:
    """执行声明式动作：解析参数 → 反解 URL → dispatch 现有视图。

    返回与专用动作一致的 ``{ok, detail, data}`` 语义（错误一律可读文案）。
    """
    from urllib.parse import urlencode

    path_params, body, query, error = resolve_api_params(spec, user, params)
    if error:
        return {"ok": False, "detail": error, "data": {}}

    url = build_action_url(spec, path_params)
    if url is None:
        logger.warning("ai api action bad declaration (unfilled placeholder). key:%s", spec.key)
        return {"ok": False, "detail": str(_("The action is not available")), "data": {}}
    if query:
        url = f"{url}?{urlencode(query, doseq=True)}"

    try:
        # resolve 只接受纯 path（带 query string 会 Resolver404），query 只进请求
        match = resolve(url.split("?", 1)[0])
    except Resolver404:
        logger.warning("ai api action path not resolvable. key:%s url:%s", spec.key, url)
        return {"ok": False, "detail": str(_("The action is not available")), "data": {}}

    factory = APIRequestFactory()
    if spec.method == "GET":
        # GET（列表/查询类动作）：参数走 query string，不带请求体（DRF 会把 body 解析进
        # request.data，与 query_params 混淆，且分页/过滤只认 query）
        request = factory.get(url)
    else:
        # 注意：generic() 默认 content_type=application/octet-stream（且无 format 参数），
        # 必须显式给 JSON content_type + 手写序列化，否则 DRF 解析器 415 拒绝。
        request = factory.generic(
            spec.method,
            url,
            data=json.dumps(body, ensure_ascii=False, default=str),
            content_type="application/json",
        )
    # 注入已认证用户（等价 DRF 的 force_authenticate）：视图的权限链照常执行，
    # 不给 AI 动作开后门；不经过认证链仅因请求由服务端内部构造。
    request._force_auth_user = user
    if spec.requires_approval(user, params):
        # 高危动作预授权：AI 层强制审批已在 action/execute 完成（412 协议消费
        # 一次性令牌后才可能走到这里），内部 dispatch 标记放行业务层
        # @ApprovalRequired，避免同一操作被重复拦截建单（见 process_approval）。
        request._approval_pre_authorized = True
    try:
        response = match.func(request, **match.kwargs)
    except Exception as exc:  # noqa: BLE001 内部 dispatch 失败一律转可读错误
        logger.warning("ai api action dispatch failed. key:%s url:%s", spec.key, url, exc_info=True)
        return {"ok": False, "detail": str(_("The action failed: {}").format(str(exc)[:200])), "data": {}}

    payload = getattr(response, "data", None) or {}
    status_code = int(getattr(response, "status_code", 500) or 500)
    code = payload.get("code") if isinstance(payload, dict) else None
    if status_code < 400 and code == 1000:
        detail = payload.get("detail")
        return {
            "ok": True,
            "detail": str(detail) if detail else str(_("Operation successful")),
            "data": payload.get("data") or {},
        }
    if status_code < 400 and code is None and isinstance(payload, dict) and "results" in payload:
        # 读类动作命中列表接口：分页响应无 ApiResponse 包装（{total, results}），
        # 结果本体即数据（data=整个分页载荷，前端表格直接渲染 results）
        return {"ok": True, "detail": str(_("Operation successful")), "data": payload}
    detail = payload.get("detail") if isinstance(payload, dict) else None
    logger.info("ai api action rejected. key:%s url:%s status:%s", spec.key, url, status_code)
    return {"ok": False, "detail": str(detail) if detail else str(_("Operation failed")), "data": {}}
