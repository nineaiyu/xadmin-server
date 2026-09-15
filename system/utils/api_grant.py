#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""应用级资源授权（开放平台二期，ADR-039）：模型 × 动作 × 字段 × 行四级收敛。

口径（只收敛不提权，fail-closed）：

- **兼容模式**：应用不存在任何 ``is_active=True`` 的授权规则时，本模块全部判定
  返回 None，调用方直接跳过——存量接入方（一期 scopes + owner 权限）零影响；
- **白名单模式**：存在规则时，请求目标模型必须被某条规则覆盖（``model`` 精确或
  ``*``），且请求命中菜单权限点的动作段必须在覆盖规则的 ``actions`` 内（或 ``*``）；
  未命中一律 PermissionDenied（未知动作段同样不放行）；
- **字段级**：覆盖规则的 ``fields`` 非空时收敛字段白名单（与用户字段权限取交集，
  且穿透「字段权限豁免」——约束挂在凭证维度，不随用户身份豁免）；
- **行级**：覆盖规则的 ``row_filter`` 非空时编译为 ``Q``，AND 叠加在数据权限过滤
  之后（对超管 owner 场景同样生效）。

四级之上仍走原有菜单/字段/数据权限（全部取交集），应用授权只能收紧，永不放大。
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from django.core.cache import cache
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import PermissionDenied

from common.core.data_scope import ScopeResult, compile_grant
from common.utils import get_logger
from system.models import Menu, ModelLabelField

logger = get_logger(__name__)

ANY = "*"
# 菜单元信息（动作段 / 绑定模型）短缓存：菜单变更在窗口内自愈，窗口外零查询
MENU_META_CACHE_TTL = 60
MENU_META_CACHE_KEY = "api_grant_menu_meta_{pk}"

# 动作段 → 中文展示名（管理面目录用；未命中回退动作段原文）
ACTION_LABELS = {
    "list": _("List"),
    "retrieve": _("Retrieve"),
    "create": _("Create"),
    "partialUpdate": _("Update"),
    "update": _("Update"),
    "destroy": _("Delete"),
    "batchDestroy": _("Batch delete"),
    "exportData": _("Export"),
    "exportAsync": _("Async export"),
    "importData": _("Import"),
    "importValidate": _("Import validate"),
    "importHeaders": _("Import headers"),
    "importAsync": _("Async import"),
}


def action_of_code(code) -> str:
    """权限点 code（``list:SystemUser``）→ 动作段（``list``）；无法解析返回空串。"""
    name = str(code or "").strip()
    if ":" not in name:
        return ""
    return name.split(":", 1)[0].strip()


def application_of_request(request):
    """当前请求若以应用凭证认证（``request.auth`` 为绑定了应用的 PAT），返回应用对象。

    - JWT / 匿名请求：request.auth 非 PAT → None（不适用四级授权）；
    - 「JWT 胜出 + 同请求带 Pat 头」：身份是 JWT，凭证不受信任，同样返回 None
      （scope 校验已在权限层单独兜底）。
    """
    auth = getattr(request, "auth", None)
    application_id = getattr(auth, "api_application_id", None)
    if not application_id:
        return None
    application = getattr(auth, "api_application", None)
    if application is None:
        logger.warning(f"api application {application_id} referenced by token but not found")
    return application


def active_grants(application):
    """应用的生效授权规则（无规则 = 兼容模式，见模块 docstring）。"""
    return list(application.grants.filter(is_active=True))


def resolve_menu_meta(menu_pk):
    """菜单权限点的 ``{"action": 动作段, "models": [模型标签]}``（短缓存）。"""
    if not menu_pk:
        return None
    cache_key = MENU_META_CACHE_KEY.format(pk=menu_pk)
    try:
        cached = cache.get(cache_key)
    except Exception:  # noqa: BLE001 缓存故障不影响判定（直查）
        cached = None
    if cached is not None:
        return cached
    menu = Menu.objects.filter(pk=menu_pk).prefetch_related("model").first()
    if menu is None:
        return None
    meta = {
        "action": action_of_code(menu.name),
        "models": sorted(label_field.name for label_field in menu.model.all() if label_field.name),
    }
    try:
        cache.set(cache_key, meta, MENU_META_CACHE_TTL)
    except Exception:  # noqa: BLE001
        pass
    return meta


def resolve_request_menu_pk(request):
    """按请求 path + method 在「启用权限菜单」里命中菜单 pk（与权限层同口径）。

    仅用于超管 / 白名单 URL 出口（权限层未解析菜单上下文）的应用凭证请求：
    两次 URL 特例（search-columns 与 list 同权、import/export 回退）与
    ``IsAuthenticated._resolve_menu_pk`` 保持一致。
    """
    url = getattr(request, "path_info", None) or getattr(request, "path", "")
    method = (getattr(request, "method", "") or "").upper()
    permission_data = {
        path: pk
        for path, pk in Menu.objects.filter(
            menu_type=Menu.MenuChoices.PERMISSION, is_active=True, method=method
        ).values_list("path", "pk")
    }
    if not permission_data:
        return None
    match = re.match("(?P<url>.*)/search-columns$", url)
    if match:
        url = match.group("url")
    menu_pk = _match_menu_pk(permission_data, url)
    if menu_pk is None:
        match = re.match("(?P<url>.*)/(export|import)-(data|async|validate|headers)$", url)
        if match:
            menu_pk = _match_menu_pk(permission_data, match.group("url"))
    return menu_pk


def _match_menu_pk(permission_data, url):
    direct = permission_data.get(f"{url[1:]}$")
    if direct:
        return direct
    for path, pk in permission_data.items():
        if re.match(f"/{path}", url):
            return pk
    return None


def resolve_request_model_label(request, view):
    """请求的目标模型标签：优先视图 ``queryset.model``（真实资源），回退菜单绑定模型。

    两者都解析不到（裸 APIView 且菜单未绑定模型）→ None：白名单模式下只有
    ``model="*"`` 的规则能覆盖（fail-closed，需管理员显式授通配）。
    """
    queryset = getattr(view, "queryset", None)
    model = getattr(queryset, "model", None)
    if model is not None:
        return model._meta.label_lower
    menu_meta = resolve_menu_meta(getattr(getattr(request, "user", None), "menu", None))
    models = (menu_meta or {}).get("models") or []
    return models[0] if len(models) == 1 else None


def match_grants(grants, model_label, action):
    """覆盖（模型 × 动作）的规则列表；白名单模式下为空 = 拒绝。"""
    matched = []
    for grant in grants:
        if grant.model != ANY and grant.model != model_label:
            continue
        actions = grant.actions or []
        if ANY not in actions and (not action or action not in actions):
            continue
        matched.append(grant)
    return matched


def grant_field_allowlist(matched, model_label):
    """字段级收敛白名单：None = 不限；set = 允许字段（精确模型规则并集）。

    任一覆盖规则 ``fields`` 为空即视为「该模型不限字段」；``*`` 规则的 fields
    在保存校验时强制为空（模型通配时字段语义不成立），此处直接跳过。
    """
    allowed = set()
    for grant in matched:
        if grant.model != model_label:
            continue
        fields = [str(item) for item in (grant.fields or []) if item]
        if not fields:
            return None
        allowed.update(fields)
    return allowed or None


def grant_row_filter(matched, model, user):
    """行级收敛：编译为 ``Q``（None = 不限）；无有效规则时 fail-closed 全拒。

    复用 DataPermission 的规则编译器（``data_scope.compile_grant``）：规则的
    ``table`` 由规则所在授权强制注入为当前模型，规则坏值编译为 DENY_ALL 即全拒。
    """
    rules = []
    for grant in matched:
        for rule in grant.row_filter or []:
            if isinstance(rule, dict):
                item = dict(rule)
                item["table"] = model._meta.label_lower
                rules.append(item)
    if not rules:
        return None
    from system.services import ModeTypeAbstract

    dp = SimpleNamespace(rules=rules, mode_type=ModeTypeAbstract.ModeChoices.OR)
    result = compile_grant(dp, model, user)
    if result is None:
        return None
    if result.kind == ScopeResult.KIND_ALLOW:
        return None
    if result.kind == ScopeResult.KIND_DENY:
        return Q(pk__isnull=True)
    return result.q


def enforce_application_grant(request, view):
    """四级授权主入口（在权限层调用）：模型 × 动作级判定 + 字段收敛挂载。

    返回匹配的规则列表（兼容模式返回 None）。命中失败抛 PermissionDenied；
    命中的规则挂到 ``request._api_grant_matched`` 供行级过滤消费，
    字段白名单挂到 ``request.api_grant_fields`` 供序列化层收敛。
    """
    application = application_of_request(request)
    if application is None:
        return None
    grants = active_grants(application)
    if not grants:
        return None
    model_label = resolve_request_model_label(request, view)
    menu_meta = resolve_menu_meta(getattr(getattr(request, "user", None), "menu", None))
    action = (menu_meta or {}).get("action") or ""
    matched = match_grants(grants, model_label, action)
    if not matched:
        logger.warning(
            "api application grant denied. application:%s model:%s action:%s path:%s",
            application.pk,
            model_label,
            action,
            getattr(request, "path", ""),
        )
        raise PermissionDenied(_("API application grant does not allow this operation"))
    request._api_grant_matched = matched
    allowlist = grant_field_allowlist(matched, model_label)
    if allowlist is not None:
        request.api_grant_fields = {model_label: allowlist}
    return matched


def apply_grant_row_scope(request, queryset):
    """行级收敛挂载点（数据权限过滤之后调用）：AND 叠加应用行级规则。"""
    matched = getattr(request, "_api_grant_matched", None)
    if not matched:
        return queryset
    model = getattr(queryset, "model", None)
    if model is None:
        return queryset
    row_q = grant_row_filter(matched, model, getattr(request, "user", None))
    if row_q is None:
        return queryset
    return queryset.filter(row_q)


def apply_grant_fields(request, model_label, allowed):
    """字段级收敛挂载点（序列化层调用）：与用户字段权限取交集（最后一道）。"""
    grant_fields = getattr(request, "api_grant_fields", None)
    if not grant_fields:
        return allowed
    allowlist = grant_fields.get(model_label)
    if allowlist is None:
        return allowed
    return set(allowed) & set(allowlist)


CATALOG_CACHE_KEY = "api_grant_catalog_v1"
CATALOG_CACHE_TTL = 60


def _catalogs():
    """(模型→动作段集合, 模型→字段集合) 目录（从启用权限菜单与字段标签树派生，短缓存）。"""
    try:
        cached = cache.get(CATALOG_CACHE_KEY)
    except Exception:  # noqa: BLE001
        cached = None
    if cached is not None:
        return cached["actions"], cached["fields"]
    actions: dict[str, set] = {}
    menus = (
        Menu.objects.filter(menu_type=Menu.MenuChoices.PERMISSION, is_active=True)
        .prefetch_related("model")
        .only("pk", "name")
    )
    for menu in menus:
        action = action_of_code(menu.name)
        if not action:
            continue
        for label_field in menu.model.all():
            if label_field.name:
                actions.setdefault(label_field.name, set()).add(action)
    fields: dict[str, set] = {}
    nodes = list(ModelLabelField.objects.filter(parent__isnull=True).values("pk", "name"))
    node_names = {node["pk"]: node["name"] for node in nodes}
    for row in ModelLabelField.objects.filter(parent_id__in=node_names.keys()).values("parent_id", "name"):
        label = node_names.get(row["parent_id"])
        if label and row["name"]:
            fields.setdefault(label, set()).add(row["name"])
    try:
        cache.set(CATALOG_CACHE_KEY, {"actions": actions, "fields": fields}, CATALOG_CACHE_TTL)
    except Exception:  # noqa: BLE001
        pass
    return actions, fields


def validate_grant_payload(model_label, actions, fields, row_filter):
    """授权规则写入校验（serializer 调用）：非法配置在保存时被拒。

    规则（与运行时判定同口径）：
    - ``model`` 必须是模型标签树中的模型或 ``*``；
    - ``actions`` 必须是该模型真实存在的动作段（从权限菜单派生）或 ``*``；
    - ``model="*"`` 时不得配置 ``fields`` / ``row_filter``（通配模型下两者语义不成立）；
    - ``fields`` 必须在该模型字段集内（空 = 全部字段）；
    - ``row_filter`` 走 data_scope 同源校验（table 强制注入为当前模型）。
    """
    from rest_framework.exceptions import ValidationError

    from common.core.data_scope import validate_rules

    model_label = str(model_label or "").strip()
    if model_label != ANY:
        known_actions, known_fields = _catalogs()
        if model_label not in known_actions and model_label not in known_fields:
            raise ValidationError(_("Unknown model: %(model)s") % {"model": model_label})
    actions = actions or []
    if not isinstance(actions, list) or not actions:
        raise ValidationError(_("Actions cannot be empty"))
    actions = [str(item).strip() for item in actions if str(item).strip()]
    if not actions:
        raise ValidationError(_("Actions cannot be empty"))
    fields = [str(item).strip() for item in (fields or []) if str(item).strip()]
    row_filter = row_filter or []
    if model_label == ANY:
        if fields or row_filter:
            raise ValidationError(_("Wildcard model does not support fields or row filter"))
        return actions, fields, row_filter
    known_actions, known_fields = _catalogs()
    allowed_actions = known_actions.get(model_label, set())
    unknown = [action for action in actions if action != ANY and action not in allowed_actions]
    if unknown:
        raise ValidationError(
            _("Unknown actions for %(model)s: %(actions)s") % {"model": model_label, "actions": ", ".join(unknown)}
        )
    if fields:
        allowed_fields = known_fields.get(model_label, set())
        unknown_fields = [field for field in fields if field not in allowed_fields]
        if unknown_fields:
            raise ValidationError(
                _("Unknown fields for %(model)s: %(fields)s")
                % {"model": model_label, "fields": ", ".join(unknown_fields)}
            )
    if row_filter:
        rules = []
        for rule in row_filter:
            if not isinstance(rule, dict):
                raise ValidationError(_("Row filter must be a list of rule objects"))
            item = dict(rule)
            item["table"] = model_label
            rules.append(item)
        validate_rules(rules)
    return actions, fields, row_filter


def grant_options_for_user(user) -> dict:
    """应用授权目录：模型 →（动作段、字段），粒度与 scope-options 同口径。

    - 数据源：模型/字段标签树（``ModelLabelField``）× 用户可授权的权限菜单；
    - 普通用户只列出本人有权限的模型/动作；超管为全部启用权限菜单；
    - ``*``（全部模型 / 全部动作）作为第一项始终可选（仅对超管可见模型全集）。
    """
    from system.utils.pat_scope import _iter_scope_menus  # 延迟导入：绕开权限层模块循环

    model_actions: dict[str, set] = {}
    for _method, menu in _iter_scope_menus(user):
        action = action_of_code(menu.name)
        if not action:
            continue
        for label_field in menu.model.all():
            if label_field.name:
                model_actions.setdefault(label_field.name, set()).add(action)

    nodes = list(ModelLabelField.objects.filter(parent__isnull=True).values("pk", "name", "label"))
    node_pks = [node["pk"] for node in nodes]
    children: dict = {}
    for row in ModelLabelField.objects.filter(parent_id__in=node_pks).values("parent_id", "name", "label"):
        children.setdefault(row["parent_id"], []).append({"value": row["name"], "label": row["label"] or row["name"]})

    models = []
    merged: dict[str, dict] = {}
    for node in nodes:
        label = node["name"]
        if label not in model_actions:
            continue
        actions = [
            {"value": action, "label": ACTIONS_DISPLAY.get(action, action)}
            for action in sorted(model_actions[label], key=_action_sort_key)
        ]
        fields = sorted(children.get(node["pk"], []), key=lambda item: item["value"])
        item = merged.get(label)
        if item is None:
            item = {
                "value": label,
                "label": node["label"] or label,
                "actions": actions,
                "fields": fields,
            }
            merged[label] = item
            models.append(item)
        else:
            known = {action["value"] for action in item["actions"]}
            item["actions"].extend(action for action in actions if action["value"] not in known)
            known_fields = {field["value"] for field in item["fields"]}
            item["fields"].extend(field for field in fields if field["value"] not in known_fields)
    models.sort(key=lambda item: item["value"])
    models.insert(
        0,
        {
            "value": ANY,
            "label": _("All models"),
            "actions": [{"value": ANY, "label": _("All actions")}],
            "fields": [],
        },
    )
    return {"total": len(models), "models": models}


def _action_sort_key(action: str):
    order = list(ACTION_LABELS)
    try:
        return (0, order.index(action), action)
    except ValueError:
        return (1, 0, action)


# 前端展示用（延迟求值：gettext_lazy 在渲染时翻译）
ACTIONS_DISPLAY = ACTION_LABELS
