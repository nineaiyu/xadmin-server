#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""OAuth 2.0 授权码模式（ADR-039 B2）：第三方代表 xadmin 用户访问。

端点（`/api/system/open/oauth/*`，白名单，视图内 fail-closed）：

- ``GET  /authorize``：同意页数据（校验 client / redirect_uri / scope / PKCE），需登录态；
- ``POST /approve``：用户同意 / 拒绝 → 一次性授权码（缓存 300s，仅存哈希映射）；
- ``POST /token``：``authorization_code`` 换 access + refresh；``refresh_token`` 一次性轮换；
- ``POST /revoke``：撤销 refresh（RFC 7009 口径）并联动失效关联 access。

权限面 = 应用 scope（接口）× 应用四级授权（ADR-039 B1）× 授权用户自身权限（交集）：
access 凭证是 PAT（creator = 授权用户，``api_application`` = 应用），走既有认证链，
凡带 ``api_application`` 的凭证都过四级门，OAuth 不另开绕过路径。

scope 参数说明：xadmin 的 scope 条目是「METHOD + 路径正则」（本身含空格），
故本实现的 ``scope`` 参数用**逗号**分隔（省略 = 应用全部 scope；提供即必须是子集）。
"""

import base64
import hashlib
import secrets
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from common.core.auth import hash_pat_token
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models.log import OperationLog
from system.models.token import ApiApplication, OAuthRefreshToken, PersonalAccessToken
from system.services import UserInfo
from system.views.open import verify_application_credentials

AUTH_CODE_TTL_SECONDS = 300
REFRESH_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30
CODE_CACHE_KEY = "oauth_authorize_code_{digest}"
PKCE_METHODS = ("S256", "plain")
OAUTH_ACCESS_PREFIX = "aoat"
OAUTH_REFRESH_PREFIX = "aort"


def oauth_error(error: str, detail=None, status: int = 400):
    """标准 OAuth 错误码 + 项目响应壳（``data.error`` 供标准客户端读取）。"""
    return ApiResponse(code=1001, detail=str(detail or error), data={"error": error}, status=status)


def _code_cache_key(code: str) -> str:
    return CODE_CACHE_KEY.format(digest=hashlib.sha256(code.encode("utf-8")).hexdigest())


def issue_authorize_code(user, application, redirect_uri, scopes, code_challenge, code_challenge_method) -> str:
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


def consume_authorize_code(code: str):
    """消费授权码（一次性）：命中即删除，返回绑定载荷；未命中/已用过返回 None。"""
    key = _code_cache_key(code)
    payload = cache.get(key)
    if payload is None:
        return None
    cache.delete(key)
    return payload


def verify_pkce(code_challenge: str, method: str, verifier: str) -> bool:
    """PKCE 校验：未启用 challenge 时直接通过；启用时 verifier 必须匹配（S256/plain）。"""
    if not code_challenge:
        return True
    if not verifier:
        return False
    if (method or "S256") == "plain":
        return secrets.compare_digest(code_challenge, verifier)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("utf-8")
    return secrets.compare_digest(code_challenge, expected)


def resolve_requested_scopes(application, scope_param):
    """请求范围 → 应用 scope 子集（省略 = 全部；提供即必须逐项命中，否则 (None, 错误)）。"""
    granted = [str(item) for item in (application.scopes or [])]
    raw = str(scope_param or "").strip()
    if not raw:
        return granted, None
    requested = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = [item for item in requested if item not in granted]
    if unknown:
        return None, _("Requested scope is not allowed: %(scope)s") % {"scope": ", ".join(unknown)}
    return requested, None


def issue_oauth_access_token(application, user, scopes):
    """为「代表用户」访问签发 access（PAT，creator = 授权用户）。"""
    now = timezone.now()
    expires_at = None
    if application.token_ttl_seconds:
        expires_at = now + timedelta(seconds=application.token_ttl_seconds)
    if application.expired_at:
        expires_at = min(expires_at, application.expired_at) if expires_at else application.expired_at
    raw_token = f"{OAUTH_ACCESS_PREFIX}_{secrets.token_urlsafe(32)}"
    token = PersonalAccessToken.objects.create(
        name=f"oauth:{application.client_id}",
        token_hash=hash_pat_token(raw_token),
        token_prefix=raw_token[:12],
        scopes=list(scopes),
        ip_allowlist=application.ip_allowlist or [],
        expired_at=expires_at,
        api_application=application,
        creator=user,
    )
    return token, raw_token


def issue_oauth_refresh_token(application, user, scopes, access_token):
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


def _validate_authorize_request(request, data) -> tuple:
    """同意页/授权请求公共校验，返回 (application, redirect_uri, scopes, challenge, method, state, error)。"""
    client_id = str(data.get("client_id") or "").strip()
    redirect_uri = str(data.get("redirect_uri") or "").strip()
    response_type = str(data.get("response_type") or "code").strip()
    if response_type != "code":
        return None, None, None, None, None, None, oauth_error("unsupported_response_type")
    application = ApiApplication.objects.filter(client_id=client_id, is_active=True).first()
    if application is None:
        return None, None, None, None, None, None, oauth_error("invalid_client")
    if not application.callback_urls or redirect_uri not in (application.callback_urls or []):
        return (
            None,
            None,
            None,
            None,
            None,
            None,
            oauth_error("invalid_request", _("redirect_uri is not registered for this application")),
        )
    scopes, error = resolve_requested_scopes(application, data.get("scope"))
    if error:
        return None, None, None, None, None, None, oauth_error("invalid_scope", error)
    code_challenge = str(data.get("code_challenge") or "").strip()
    code_challenge_method = str(data.get("code_challenge_method") or "S256").strip()
    if code_challenge and code_challenge_method not in PKCE_METHODS:
        return (
            None,
            None,
            None,
            None,
            None,
            None,
            oauth_error("invalid_request", _("Unsupported code_challenge_method")),
        )
    state = str(data.get("state") or "")
    return application, redirect_uri, scopes, code_challenge, code_challenge_method, state, None


def write_oauth_audit(request, application, result: str) -> None:
    """授权动作审计（approve/deny 各一条，module=OAuth）；异常不外抛。"""
    try:
        OperationLog.objects.create(
            module="OAuth",
            path=request.path,
            method=request.method,
            object_pk=str(application.pk),
            status_code=1000,
            response_result=result,
            creator=request.user,
            request_uuid=getattr(request, "request_uuid", None),
        )
    except Exception:  # noqa: BLE001 审计失败不影响授权链路
        pass


class OpenOAuthAuthorizeAPIView(APIView):
    """同意页数据：校验请求参数并回显应用、请求范围与当前用户（登录态）。"""

    @extend_schema(responses=get_default_response_schema())
    def get(self, request, *args, **kwargs):
        application, redirect_uri, scopes, challenge, method, state, error = _validate_authorize_request(
            request, request.query_params
        )
        if error is not None:
            return error
        return ApiResponse(
            data={
                "application": {"client_id": application.client_id, "name": application.name},
                "scopes": scopes,
                "user": {"pk": request.user.pk, "username": request.user.username},
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge_required": bool(challenge),
                "code_challenge_method": method if challenge else "",
            }
        )


class OpenOAuthApproveAPIView(APIView):
    """用户同意 / 拒绝：同意生成一次性授权码；拒绝回传 access_denied（均写审计）。"""

    def post(self, request, *args, **kwargs):
        approved = request.data.get("approved")
        approved = approved is True or str(approved).lower() in ("true", "1", "yes")
        application, redirect_uri, scopes, challenge, method, state, error = _validate_authorize_request(
            request, request.data
        )
        if error is not None:
            return error
        if not approved:
            write_oauth_audit(request, application, "denied")
            return ApiResponse(
                data={"redirect_uri": redirect_uri, "state": state, "error": "access_denied", "code": ""}
            )
        code = issue_authorize_code(request.user, application, redirect_uri, scopes, challenge, method)
        write_oauth_audit(request, application, "approved")
        return ApiResponse(data={"code": code, "redirect_uri": redirect_uri, "state": state})


class OpenOAuthTokenAPIView(APIView):
    """授权码 / 刷新令牌换发（匿名可达，凭 client 凭据 + code/refresh 双重校验）。"""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        client_id = str(request.data.get("client_id") or "").strip()
        client_secret = str(request.data.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            return oauth_error("invalid_client", status=401)
        application, error = verify_application_credentials(client_id, client_secret)
        if application is None:
            return oauth_error("invalid_client", error, status=401)
        grant_type = str(request.data.get("grant_type") or "").strip()
        if grant_type == "authorization_code":
            return self._exchange_code(request, application)
        if grant_type == "refresh_token":
            return self._refresh(request, application)
        return oauth_error("unsupported_grant_type")

    def _exchange_code(self, request, application):
        code = str(request.data.get("code") or "").strip()
        redirect_uri = str(request.data.get("redirect_uri") or "").strip()
        verifier = str(request.data.get("code_verifier") or "")
        payload = consume_authorize_code(code) if code else None
        if payload is None:
            return oauth_error("invalid_grant", _("Authorization code is invalid or expired"))
        if payload["application_pk"] != str(application.pk) or payload["redirect_uri"] != redirect_uri:
            return oauth_error("invalid_grant", _("Authorization code does not match this client"))
        if not verify_pkce(payload.get("code_challenge"), payload.get("code_challenge_method"), verifier):
            return oauth_error("invalid_grant", _("PKCE verification failed"))
        user = UserInfo.objects.filter(pk=payload["user_pk"], is_active=True).first()
        if user is None:
            return oauth_error("invalid_grant", _("Authorized user is disabled"))
        scopes = payload.get("scopes") or []
        access, raw_access = issue_oauth_access_token(application, user, scopes)
        _refresh_row, raw_refresh = issue_oauth_refresh_token(application, user, scopes, access)
        return ApiResponse(data=self._token_payload(application, raw_access, access, raw_refresh, scopes))

    def _refresh(self, request, application):
        raw_refresh = str(request.data.get("refresh_token") or "").strip()
        row = (
            OAuthRefreshToken.objects.filter(token_hash=hash_pat_token(raw_refresh), application=application)
            .select_related("user")
            .first()
            if raw_refresh
            else None
        )
        now = timezone.now()
        if row is None or row.is_revoked or (row.expired_at and row.expired_at <= now):
            return oauth_error("invalid_grant", _("Refresh token is invalid or revoked"))
        user = row.user
        if user is None or not user.is_active:
            return oauth_error("invalid_grant", _("Authorized user is disabled"))
        with transaction.atomic():
            # 一次性轮换：旧 refresh 失效 + 关联 access 失效
            row.is_revoked = True
            row.save(update_fields=["is_revoked", "updated_time"])
            if row.access_token_id:
                PersonalAccessToken.objects.filter(pk=row.access_token_id).update(is_active=False)
            scopes = row.scopes or []
            access, raw_access = issue_oauth_access_token(application, user, scopes)
            _refresh_row, raw_refresh_new = issue_oauth_refresh_token(application, user, scopes, access)
        return ApiResponse(data=self._token_payload(application, raw_access, access, raw_refresh_new, scopes))

    @staticmethod
    def _token_payload(application, raw_access, access_token, raw_refresh, scopes) -> dict:
        expires_in = (
            int((access_token.expired_at - timezone.now()).total_seconds()) if access_token.expired_at else None
        )
        return {
            "access_token": raw_access,
            "token_type": "Pat",
            "expires_in": expires_in,
            "refresh_token": raw_refresh,
            "refresh_expires_in": REFRESH_TOKEN_TTL_SECONDS,
            "scope": scopes,
        }


class OpenOAuthRevokeAPIView(APIView):
    """撤销（RFC 7009）：优先 refresh（联动失效关联 access），其次 access 凭证本身。"""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        client_id = str(request.data.get("client_id") or "").strip()
        client_secret = str(request.data.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            return oauth_error("invalid_client", status=401)
        application, error = verify_application_credentials(client_id, client_secret)
        if application is None:
            return oauth_error("invalid_client", error, status=401)
        raw_token = str(request.data.get("token") or "").strip()
        if not raw_token:
            return oauth_error("invalid_request", _("token is required"))
        digest = hash_pat_token(raw_token)
        row = OAuthRefreshToken.objects.filter(token_hash=digest, application=application).first()
        if row is not None:
            with transaction.atomic():
                row.is_revoked = True
                row.save(update_fields=["is_revoked", "updated_time"])
                if row.access_token_id:
                    PersonalAccessToken.objects.filter(pk=row.access_token_id).update(is_active=False)
            return ApiResponse(data={"revoked": True})
        updated = PersonalAccessToken.objects.filter(
            token_hash=digest, api_application=application, is_active=True
        ).update(is_active=False)
        # RFC 7009：未知 token 也返回成功（revoked=False 供观测，不向调用方暴露存在性）
        return ApiResponse(data={"revoked": bool(updated)})
