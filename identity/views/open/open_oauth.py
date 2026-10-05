#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""OAuth 2.0 授权码模式：第三方代表 xadmin 用户访问。

端点（`/api/system/open/oauth/*`，白名单，视图内 fail-closed）：

- ``GET  /authorize``：同意页数据（校验 client / redirect_uri / scope / PKCE），需登录态；
- ``POST /approve``：用户同意 / 拒绝 → 一次性授权码（缓存 300s，仅存哈希映射）；
- ``POST /token``：``authorization_code`` 换 access + refresh；``refresh_token`` 一次性轮换；
- ``POST /revoke``：撤销 refresh（RFC 7009 口径）并联动失效关联 access。

本模块只保留请求解析、凭据校验入口、响应构造与授权审计；协议引擎
（授权码生命周期 / PKCE / scope / 双令牌签发编排）见 ``system.services.open_oauth``。

scope 参数说明：xadmin 的 scope 条目是「METHOD + 路径正则」（本身含空格），
故本实现的 ``scope`` 参数用**逗号**分隔（省略 = 应用全部 scope；提供即必须是子集）。
"""

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from common.core.response import ApiResponse
from common.core.throttle import OAuthClientThrottle
from common.swagger.utils import get_default_response_schema
from identity.services.open_oauth import (
    exchange_authorization_code,
    issue_authorize_code,
    revoke_granted_token,
    rotate_refresh_token,
    validate_authorize_request,
)
from identity.utils.pat_scope import scope_display_value
from identity.views.open.open import verify_application_credentials
from system.services import OperationLog


def oauth_error(error: str, detail=None, status: int = 400):
    """标准 OAuth 错误码 + 项目响应壳（``data.error`` 供标准客户端读取）。"""
    return ApiResponse(code=1001, detail=str(detail or error), data={"error": error}, status=status)


def _oauth_response(result, error):
    """协议结果 → 响应：错误三元组映射 oauth_error，成功载荷原样返回。"""
    if error is not None:
        code, detail, status = error
        return oauth_error(code, detail, status)
    return ApiResponse(data=result)


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
        application, redirect_uri, scopes, challenge, method, state, error = validate_authorize_request(
            request.query_params
        )
        if error is not None:
            return _oauth_response(None, error)
        return ApiResponse(
            data={
                "application": {"client_id": application.client_id, "name": application.name},
                # 同意页展示用可读形态（存储与判定仍是锚定正则，见 normalize_scope_entry）
                "scopes": [scope_display_value(item) for item in scopes],
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
        application, redirect_uri, scopes, challenge, method, state, error = validate_authorize_request(request.data)
        if error is not None:
            return _oauth_response(None, error)
        if not approved:
            write_oauth_audit(request, application, "denied")
            return ApiResponse(
                data={"redirect_uri": redirect_uri, "state": state, "error": "access_denied", "code": ""}
            )
        code = issue_authorize_code(request.user, application, redirect_uri, scopes, challenge, method)
        write_oauth_audit(request, application, "approved")
        return ApiResponse(data={"code": code, "redirect_uri": redirect_uri, "state": state})


class OpenOAuthTokenAPIView(APIView):
    """授权码 / 刷新令牌换发（匿名可达，凭 client 凭据 + code/refresh 双重校验）。

    保留全局匿名限流（IP 维度），叠加 client 维度专用限流（授权码/刷新
    换发为登录高峰共享桶，速率覆盖正常峰值）。
    """

    authentication_classes: list[type] = []
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle, OAuthClientThrottle]

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
            result, error = exchange_authorization_code(
                application,
                str(request.data.get("code") or "").strip(),
                str(request.data.get("redirect_uri") or "").strip(),
                str(request.data.get("code_verifier") or ""),
            )
            return _oauth_response(result, error)
        if grant_type == "refresh_token":
            result, error = rotate_refresh_token(application, str(request.data.get("refresh_token") or "").strip())
            return _oauth_response(result, error)
        return oauth_error("unsupported_grant_type")


class OpenOAuthRevokeAPIView(APIView):
    """撤销（RFC 7009）：优先 refresh（联动失效关联 access），其次 access 凭证本身。

    同 token 端点挂 client 维度专用限流（登出风暴场景速率已覆盖）。
    """

    authentication_classes: list[type] = []
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle, OAuthClientThrottle]

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
        return ApiResponse(data={"revoked": revoke_granted_token(application, raw_token)})
