#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""应用级资源授权（开放平台二期）：模型 × 动作 × 字段 × 行四级收敛。

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
from common.core.utils import permission_path_matches
from common.utils import get_logger
from system.models import Menu
from system.utils.identity.api_grant_catalog import (  # noqa: F401 目录/选项拆出后保持既有导入面
    ACTION_LABELS,
    ACTIONS_DISPLAY,
    ANY,
    _catalogs,
    action_of_code,
    grant_options_for_user,
    validate_grant_payload,
)

logger = get_logger(__name__)

# 菜单元信息（动作段 / 绑定模型）短缓存：菜单变更在窗口内自愈，窗口外零查询
MENU_META_CACHE_TTL = 60
MENU_META_CACHE_KEY = "api_grant_menu_meta_{pk}"
# 权限菜单 path→pk 映射（按 HTTP 方法维度）短缓存：仅服务「超管 / 白名单 URL 出口的
# 应用凭证请求」的按地址回查；菜单变更经信号即时失效（窗口内自愈）。
MENU_PATH_CACHE_TTL = 60
MENU_PATH_CACHE_KEY = "api_grant_menu_paths_{method}"
MENU_PATH_CACHE_METHODS = ("GET", "PUT", "DELETE", "POST", "PATCH")


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


def _permission_path_pk_map(method: str):
    """「启用权限菜单」的 ``path → pk`` 映射（按 HTTP 方法维度，短缓存）。

    缓存故障不影响判定（降级直查）；空映射同样入缓存（避免无规则时穿透）。
    """
    cache_key = MENU_PATH_CACHE_KEY.format(method=method)
    try:
        data = cache.get(cache_key)
    except Exception:  # noqa: BLE001 缓存故障不影响判定（直查）
        data = None
    if data is not None:
        return data
    data = {
        path: pk
        for path, pk in Menu.objects.filter(
            menu_type=Menu.MenuChoices.PERMISSION, is_active=True, method=method
        ).values_list("path", "pk")
    }
    try:
        cache.set(cache_key, data, MENU_PATH_CACHE_TTL)
    except Exception:  # noqa: BLE001
        pass
    return data


def invalid_menu_path_cache():
    """失效权限菜单 ``path → pk`` 映射缓存（全部方法维度；菜单变更信号调用）。"""
    try:
        cache.delete_many([MENU_PATH_CACHE_KEY.format(method=method) for method in MENU_PATH_CACHE_METHODS])
    except Exception:  # noqa: BLE001
        pass


def resolve_request_menu_pk(request):
    """按请求 path + method 在「启用权限菜单」里命中菜单 pk（与权限层同口径）。

    仅用于超管 / 白名单 URL 出口（权限层未解析菜单上下文）的应用凭证请求：
    两次 URL 特例（search-columns 与 list 同权、import/export 回退）与
    ``IsAuthenticated._resolve_menu_pk`` 保持一致。
    """
    url = str(getattr(request, "path_info", None) or getattr(request, "path", "") or "")
    method = (getattr(request, "method", "") or "").upper()
    permission_data = _permission_path_pk_map(method)
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
    """按请求地址命中菜单 pk：精确 `path$` 优先，其次段边界前缀回退。

    回退匹配复用 ``common.core.utils.permission_path_matches``（与运行期判定、
    权限点扫描同源，单点口径）：历史实现为无锚定 ``re.match``，`api/user` 会
    粘连命中 `/api/userfoo` 并把动作段错配，让未授权动作被误放。
    """
    direct = permission_data.get(f"{url[1:]}$")
    if direct:
        return direct
    for path, pk in permission_data.items():
        if permission_path_matches(path, url):
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
