#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""开放平台雏形（ADR-030，G11）：client-credentials 应用 + 凭证换发 + 回调测试。

设计要点（细节见 docs/adr/ADR-030-open-platform.md）：
- 应用不携带权限：换发出的凭证以 owner（creator）身份走既有 PAT 认证链
  （`Authorization: Pat <token>`），三层权限/数据权限/审计天然生效；
- 换发 = **轮换**：明文不可回读，故每次换发都失效旧凭证再发新凭证（避免「以为复用、其实是旧密文」）；
- 应用停用/过期、按应用限流在 PAT 认证类内即时校验（common/core/auth.py）；
- 回调测试复用 webhook 的 HMAC-SHA256 时间戳签名口径（system/utils/webhook.py）。
"""

import json
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from common.core.auth import hash_pat_token
from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse
from system.models.token import ApiApplication, PersonalAccessToken
from system.serializers.token import ApiApplicationSerializer
from system.utils.webhook import decrypt_secret, encrypt_secret, sign_payload

CLIENT_SECRET_PREFIX = "aps"
CALLBACK_TIMEOUT_SECONDS = 10


def build_client_credentials() -> tuple[str, str, str, str]:
    """生成 client_id / client_secret 明文与落库哈希、前缀（复用 PAT 的 sha256 口径）。"""
    client_id = f"app_{secrets.token_hex(8)}"
    raw_secret = f"{CLIENT_SECRET_PREFIX}_{secrets.token_urlsafe(32)}"
    return client_id, raw_secret, hash_pat_token(raw_secret), raw_secret[:12]


def build_callback_secret() -> tuple[str, str]:
    """生成回调签名密钥（密文存储，明文只在创建/重置响应返回一次）。"""
    raw_secret = f"apc_{secrets.token_urlsafe(32)}"
    return raw_secret, encrypt_secret(raw_secret)


def issue_application_token(application: ApiApplication) -> tuple[PersonalAccessToken, str]:
    """为应用轮换一条 PAT 凭证：失效旧凭证 → 新建（明文只返回一次）。"""
    now = timezone.now()
    expires_at = None
    if application.token_ttl_seconds:
        expires_at = now + timedelta(seconds=application.token_ttl_seconds)
    if application.expired_at:
        expires_at = min(expires_at, application.expired_at) if expires_at else application.expired_at
    raw_token = f"{CLIENT_SECRET_PREFIX}t_{secrets.token_urlsafe(32)}"
    with transaction.atomic():
        PersonalAccessToken.objects.filter(api_application=application, is_active=True).update(is_active=False)
        token = PersonalAccessToken.objects.create(
            name=f"app:{application.client_id}",
            token_hash=hash_pat_token(raw_token),
            token_prefix=raw_token[:12],
            scopes=application.scopes or [],
            ip_allowlist=application.ip_allowlist or [],
            expired_at=expires_at,
            api_application=application,
            creator=application.creator,
        )
    return token, raw_token


def send_test_callback(application: ApiApplication, url: str, client=None) -> dict:
    """向单个回调地址投递一次签名探测（返回值 = 投递结果，供管理页展示）。"""
    secret = decrypt_secret(application.callback_secret_encrypted) if application.callback_secret_encrypted else ""
    body = json.dumps(
        {
            "event": "api_application.test",
            "client_id": application.client_id,
            "name": application.name,
            "timestamp": timezone.now().isoformat(),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    signature, timestamp = sign_payload(secret, body)
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature,
        "X-Webhook-Timestamp": str(timestamp),
    }
    if client is None:  # 惰性导入：requests 仅投递路径需要
        import requests

        client = requests
    try:
        response = client.post(url, data=body, headers=headers, timeout=CALLBACK_TIMEOUT_SECONDS)
        return {"url": url, "success": 200 <= response.status_code < 300, "status_code": response.status_code}
    except Exception as exc:  # noqa: BLE001 网络异常按失败结果返回，不打断管理页
        return {"url": url, "success": False, "detail": str(exc)}


class ApiApplicationTokenAPIView(APIView):
    """换发端点（client-credentials）：凭 client_id/client_secret 换 PAT 凭证。

    匿名可达（白名单 + AllowAny）：凭证即身份，与登录接口同口径。
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    @staticmethod
    def _unauthorized(detail=None):
        """凭据类失败一律 401（无认证类的视图抛 AuthenticationFailed 会被 DRF 归一为 403）。"""
        return ApiResponse(code=1001, detail=detail or _("Invalid client credentials"), status=401)

    def post(self, request, *args, **kwargs):
        """校验应用凭据并轮换签发凭证（旧凭证即时失效）。"""
        client_id = str(request.data.get("client_id") or "").strip()
        client_secret = str(request.data.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            return self._unauthorized()
        application = ApiApplication.objects.filter(client_id=client_id).select_related("creator").first()
        if application is None or application.client_secret_hash != hash_pat_token(client_secret):
            return self._unauthorized()
        now = timezone.now()
        if not application.is_active or (application.expired_at and application.expired_at <= now):
            return self._unauthorized(_("Application is disabled or expired"))
        if application.creator is None or not application.creator.is_active:
            return self._unauthorized(_("Application owner account is disabled"))
        token, raw_token = issue_application_token(application)
        expires_in = int((token.expired_at - now).total_seconds()) if token.expired_at else None
        return ApiResponse(
            data={
                "access_token": raw_token,
                "token_type": "Pat",
                "expires_in": expires_in,
                "scopes": application.scopes or [],
            }
        )


class ApiApplicationViewSet(BaseModelSet):
    """API 应用（开放平台，ADR-030）"""

    queryset = ApiApplication.objects.all()
    serializer_class = ApiApplicationSerializer
    ordering_fields = ["created_time"]
    ordering = ["-created_time"]

    def create(self, request, *args, **kwargs):
        """创建应用：client_id / client_secret / callback_secret 由服务端生成，明文仅此一次。"""
        client_id, raw_secret, secret_hash, secret_prefix = build_client_credentials()
        raw_callback_secret, callback_secret_encrypted = build_callback_secret()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(
            creator=request.user,
            client_id=client_id,
            client_secret_hash=secret_hash,
            client_secret_prefix=secret_prefix,
            callback_secret_encrypted=callback_secret_encrypted,
        )
        data = dict(serializer.data)
        data["client_secret"] = raw_secret
        data["callback_secret"] = raw_callback_secret
        return ApiResponse(data=data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        """应用停用与凭证联动：停用即失效其全部有效凭证（即时生效）。"""
        application = serializer.save()
        if not application.is_active:
            PersonalAccessToken.objects.filter(api_application=application, is_active=True).update(is_active=False)

    @action(methods=["post"], detail=True, url_path="regenerate-secret")
    def regenerate_secret(self, request, *args, **kwargs):
        """重置应用密钥：旧凭证与旧密钥即时失效，新密钥明文仅本次返回。"""
        application = self.get_object()
        client_id, raw_secret, secret_hash, secret_prefix = build_client_credentials()
        raw_callback_secret, callback_secret_encrypted = build_callback_secret()
        with transaction.atomic():
            PersonalAccessToken.objects.filter(api_application=application, is_active=True).update(is_active=False)
            application.client_id = client_id
            application.client_secret_hash = secret_hash
            application.client_secret_prefix = secret_prefix
            application.callback_secret_encrypted = callback_secret_encrypted
            application.save(
                update_fields=[
                    "client_id",
                    "client_secret_hash",
                    "client_secret_prefix",
                    "callback_secret_encrypted",
                    "updated_time",
                ]
            )
        return ApiResponse(
            data={"client_id": client_id, "client_secret": raw_secret, "callback_secret": raw_callback_secret}
        )

    @action(methods=["post"], detail=True, url_path="test-callback")
    def test_callback(self, request, *args, **kwargs):
        """向登记的回调地址逐一投递签名探测（HMAC-SHA256 时间戳签名，同出站 webhook 口径）。"""
        application = self.get_object()
        urls = [str(url) for url in (application.callback_urls or [])]
        if not urls:
            return ApiResponse(code=1001, detail=_("No callback url configured"), data={"results": []})
        results = [send_test_callback(application, url) for url in urls]
        return ApiResponse(data={"results": results})
