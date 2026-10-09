#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""OAuth 2.0 授权码协议引擎（``/api/identity/open/oauth/*`` 的领域逻辑）。

端点行为（白名单，视图内 fail-closed）：

- ``GET  /authorize``：同意页数据（校验 client / redirect_uri / scope / PKCE），需登录态；
- ``POST /approve``：用户同意 / 拒绝 → 一次性授权码（缓存 300s，仅存哈希映射）；
- ``POST /token``：``authorization_code`` 换 access + refresh；``refresh_token`` 一次性轮换；
- ``POST /revoke``：撤销 refresh（RFC 7009 口径）并联动失效关联 access。

视图层只保留请求解析、凭据校验入口与响应构造；授权码生命周期、PKCE、scope
解析与双令牌签发编排收口到本模块。权限面 = 应用 scope（接口）× 应用四级授权
× 授权用户自身权限（交集）：access 凭证是 PAT（creator = 授权用户，
``api_application`` = 应用），走既有认证链，凡带 ``api_application`` 的凭证都
过四级门，OAuth 不另开绕过路径。

协议错误以 ``(error_code, detail, status)`` 三元组返回（detail 可为 None，
视图层映射为标准 OAuth 错误码 + 项目响应壳），服务层不构造 HTTP 响应。
"""

import base64
import hashlib
import secrets
from datetime import timedelta
from typing import Any

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.core.auth import hash_pat_token, normalize_scope_entry
from identity.models.token import ApiApplication, OAuthRefreshToken, PersonalAccessToken
from identity.services.token_issue import OAUTH_ACCESS_PREFIX, issue_access_token

AUTH_CODE_TTL_SECONDS = 300
REFRESH_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30
CODE_CACHE_KEY = "oauth_authorize_code_{digest}"
#: PKCE 方法白名单：仅 S256（RFC 8252 / OAuth 2.0 Security BCP 建议）
PKCE_METHODS = ("S256",)
OAUTH_REFRESH_PREFIX = "aort"


def _authorized_user(user_pk: Any) -> Any:
    """授权码绑定的在用用户；不存在或已停用返回 None。"""
    from identity.models import UserInfo

    return UserInfo.objects.filter(pk=user_pk, is_active=True).first()


def _code_cache_key(code: str) -> str:
    return CODE_CACHE_KEY.format(digest=hashlib.sha256(code.encode("utf-8")).hexdigest())


def issue_authorize_code(
    user: Any, application: Any, redirect_uri: Any, scopes: Any, code_challenge: Any, code_challenge_method: Any
) -> str:
    """生成一次性授权码（缓存键 = 码哈希，300s）。"""
    code = secrets.token_urlsafe(32)
    cache.set(
        _code_cache_key(code),
        {
            "user_pk": user.pk,
            "application_pk": str(application.pk),
            "redirect_uri": redirect_uri,
            "scopes": list(scopes),
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
        },
        AUTH_CODE_TTL_SECONDS,
    )
    return code


def consume_authorize_code(code: str) -> Any:
    """消费授权码（一次性）：命中即删除，返回绑定载荷；未命中/已用过返回 None。"""
    key = _code_cache_key(code)
    payload = cache.get(key)
    if payload is None:
        return None
    cache.delete(key)
    return payload


def verify_pkce(code_challenge: str, method: str, verifier: str) -> bool:
    """PKCE 校验：未启用 challenge 时直接通过；启用时**仅接受 S256**。

    ``plain`` 已移除（RFC 8252 / OAuth 2.0 Security BCP 建议仅 S256：plain 下
    授权码被截获即可直接重放，等同无效防护）。方法非 S256（含历史缓存中的 plain
    授权码）一律 fail-closed；方法缺省按 S256 处理（与授权请求侧默认一致）。
    """
    if not code_challenge:
        return True
    if not verifier:
        return False
    if (method or "S256") != "S256":
        return False
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("utf-8")
    return secrets.compare_digest(code_challenge, expected)


def resolve_requested_scopes(application: Any, scope_param: Any) -> Any:
    """请求范围 → 应用 scope 子集（省略 = 全部；提供即必须逐项命中，否则 (None, 错误)）。

    比对与返回均按**锚定形态**：应用 scope 保存时已归一化（见
    ``common.core.auth.normalize_scope_entry``），客户端按可读形态请求
    （``api/identity/user``）与已锚定条目等价；签发凭证时口径统一落锚定值。
    """
    granted = [str(item) for item in (application.scopes or [])]
    raw = str(scope_param or "").strip()
    if not raw:
        return granted, None
    requested = [item.strip() for item in raw.split(",") if item.strip()]
    allowed = set(granted)
    for item in granted:
        try:
            allowed.add(normalize_scope_entry(item))
        except ValueError:  # 库内历史非法条目：忽略该条，其余条目照常
            continue
    resolved = []
    unknown = []
    for item in requested:
        try:
            normalized = normalize_scope_entry(item)
        except ValueError:
            normalized = ""
        if item in allowed or (normalized and normalized in allowed):
            resolved.append(normalized or item)
        else:
            unknown.append(item)
    if unknown:
        return None, _("Requested scope is not allowed: %(scope)s") % {"scope": ", ".join(unknown)}
    return resolved, None


def issue_oauth_access_token(application: Any, user: Any, scopes: Any) -> Any:
    """为「代表用户」访问签发 access（PAT，creator = 授权用户）。"""
    return issue_access_token(
        creator=user,
        name=f"oauth:{application.client_id}",
        prefix=OAUTH_ACCESS_PREFIX,
        scopes=scopes,
        ip_allowlist=application.ip_allowlist or [],
        expired_at=application_token_expiry(application),
        api_application=application,
    )


def application_token_expiry(application: Any) -> Any:
    """OAuth access 过期时间：应用 TTL 与应用有效期取更早者；均未设置为永不过期。"""
    expires_at = None
    if application.token_ttl_seconds:
        expires_at = timezone.now() + timedelta(seconds=application.token_ttl_seconds)
    if application.expired_at:
        expires_at = min(expires_at, application.expired_at) if expires_at else application.expired_at
    return expires_at


def issue_oauth_refresh_token(application: Any, user: Any, scopes: Any, access_token: Any) -> Any:
    """签发刷新令牌（只存哈希；一次性轮换，撤销联动失效 access）。"""
    raw_token = f"{OAUTH_REFRESH_PREFIX}_{secrets.token_urlsafe(32)}"
    row = OAuthRefreshToken.objects.create(
        token_hash=hash_pat_token(raw_token),
        application=application,
        user=user,
        scopes=list(scopes),
        expired_at=timezone.now() + timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS),
        access_token=access_token,
        creator=user,
    )
    return row, raw_token


def validate_authorize_request(data: Any) -> tuple[Any, ...]:
    """同意页/授权请求公共校验，返回 (application, redirect_uri, scopes, challenge, method, state, error)。

    ``error`` 为协议错误三元组 ``(error_code, detail, status)``（无错为 None），
    由视图层映射为响应。
    """
    client_id = str(data.get("client_id") or "").strip()
    redirect_uri = str(data.get("redirect_uri") or "").strip()
    response_type = str(data.get("response_type") or "code").strip()
    if response_type != "code":
        return None, None, None, None, None, None, ("unsupported_response_type", None, 400)
    application = ApiApplication.objects.filter(client_id=client_id, is_active=True).first()
    if application is None:
        return None, None, None, None, None, None, ("invalid_client", None, 400)
    if not application.callback_urls or redirect_uri not in (application.callback_urls or []):
        return (
            None,
            None,
            None,
            None,
            None,
            None,
            ("invalid_request", _("redirect_uri is not registered for this application"), 400),
        )
    scopes, error = resolve_requested_scopes(application, data.get("scope"))
    if error:
        return None, None, None, None, None, None, ("invalid_scope", error, 400)
    code_challenge = str(data.get("code_challenge") or "").strip()
    code_challenge_method = str(data.get("code_challenge_method") or "S256").strip()
    if code_challenge and code_challenge_method not in PKCE_METHODS:
        return None, None, None, None, None, None, ("invalid_request", _("Unsupported code_challenge_method"), 400)
    state = str(data.get("state") or "")
    return application, redirect_uri, scopes, code_challenge, code_challenge_method, state, None


def _revoke_refresh_row(row: Any) -> None:
    """一次性失效刷新令牌并联动失效关联 access（轮换 / 撤销共用）。"""
    with transaction.atomic():
        row.is_revoked = True
        row.save(update_fields=["is_revoked", "updated_time"])
        if row.access_token_id:
            PersonalAccessToken.objects.filter(pk=row.access_token_id).update(is_active=False)


def token_payload(
    application: Any, raw_access: Any, access_token: Any, raw_refresh: Any, scopes: Any
) -> dict[str, Any]:
    """/token 响应载荷（授权码与 refresh 换发共用形状）。"""
    expires_in = int((access_token.expired_at - timezone.now()).total_seconds()) if access_token.expired_at else None
    return {
        "access_token": raw_access,
        "token_type": "Pat",
        "expires_in": expires_in,
        "refresh_token": raw_refresh,
        "refresh_expires_in": REFRESH_TOKEN_TTL_SECONDS,
        "scope": scopes,
    }


def exchange_authorization_code(application: Any, code: Any, redirect_uri: Any, verifier: Any) -> Any:
    """授权码换发双令牌：一次性消费 + 客户端/redirect 绑定 + PKCE + 用户可用性。

    返回 ``(token 载荷, 错误三元组)``，载荷非 None 时错误为 None，反之亦然。
    """
    payload = consume_authorize_code(code) if code else None
    if payload is None:
        return None, ("invalid_grant", _("Authorization code is invalid or expired"), 400)
    if payload["application_pk"] != str(application.pk) or payload["redirect_uri"] != redirect_uri:
        return None, ("invalid_grant", _("Authorization code does not match this client"), 400)
    if not verify_pkce(payload.get("code_challenge"), payload.get("code_challenge_method"), verifier):
        return None, ("invalid_grant", _("PKCE verification failed"), 400)
    user = _authorized_user(payload["user_pk"])
    if user is None:
        return None, ("invalid_grant", _("Authorized user is disabled"), 400)
    scopes = payload.get("scopes") or []
    access, raw_access = issue_oauth_access_token(application, user, scopes)
    _refresh_row, raw_refresh = issue_oauth_refresh_token(application, user, scopes, access)
    return token_payload(application, raw_access, access, raw_refresh, scopes), None


def rotate_refresh_token(application: Any, raw_refresh: Any) -> Any:
    """刷新令牌一次性轮换：旧 refresh + 关联 access 同步失效，事务内签发新双令牌。

    返回 ``(token 载荷, 错误三元组)``，语义同 :func:`exchange_authorization_code`。
    """
    row = (
        OAuthRefreshToken.objects.filter(token_hash=hash_pat_token(raw_refresh), application=application)
        .select_related("user")
        .first()
        if raw_refresh
        else None
    )
    now = timezone.now()
    if row is None or row.is_revoked or (row.expired_at and row.expired_at <= now):
        return None, ("invalid_grant", _("Refresh token is invalid or revoked"), 400)
    user = row.user
    if user is None or not user.is_active:
        return None, ("invalid_grant", _("Authorized user is disabled"), 400)
    with transaction.atomic():
        _revoke_refresh_row(row)
        scopes = row.scopes or []
        access, raw_access = issue_oauth_access_token(application, user, scopes)
        _refresh_row, raw_refresh_new = issue_oauth_refresh_token(application, user, scopes, access)
    return token_payload(application, raw_access, access, raw_refresh_new, scopes), None


def revoke_granted_token(application: Any, raw_token: Any) -> bool:
    """撤销（RFC 7009）：优先 refresh（联动失效关联 access），其次 access 凭证本身。

    返回响应 ``revoked`` 标志：refresh 命中恒为 True；access 侧命中才为 True
    （RFC 7009 对未知 token 也返回成功，revoked=False 供观测，不暴露存在性）。
    """
    digest = hash_pat_token(raw_token)
    row = OAuthRefreshToken.objects.filter(token_hash=digest, application=application).first()
    if row is not None:
        _revoke_refresh_row(row)
        return True
    updated = PersonalAccessToken.objects.filter(token_hash=digest, api_application=application, is_active=True).update(
        is_active=False
    )
    return bool(updated)
