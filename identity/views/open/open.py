#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""开放平台雏形：client-credentials 应用 + 凭证换发 + 回调测试。

设计要点：
- 应用不携带权限：换发出的凭证以 owner（creator）身份走既有 PAT 认证链
  （`Authorization: Pat <token>`），三层权限/数据权限/审计天然生效；
- 换发 = **轮换**：明文不可回读，故每次换发都失效旧凭证再发新凭证（避免「以为复用、其实是旧密文」）；
- 应用停用/过期、按应用限流在 PAT 认证类内即时校验（packages/xadmin-common/common/core/auth.py）；
- 回调测试复用 webhook 的 HMAC-SHA256 时间戳签名口径（task/utils/webhook.py）。
"""

import hmac
import json
import secrets
import time
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from common.core.auth import hash_pat_token
from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet
from common.core.response import API_SUCCESS_CODE, ApiResponse
from common.core.throttle import OpenClientThrottle
from common.swagger.utils import get_default_response_schema
from identity.models.token import ApiApplication, PersonalAccessToken
from identity.serializers.token import ApiApplicationGrantSerializer, ApiApplicationSerializer
from identity.services.token_issue import issue_application_token, revoke_application_tokens
from identity.utils.api_grant import grant_options_for_user
from identity.utils.pat_scope import scope_options_for_user
from task.services import decrypt_secret, encrypt_secret, sign_payload

CLIENT_SECRET_PREFIX = "aps"
CALLBACK_TIMEOUT_SECONDS = 10
#: 多地址串行探测的总预算：单个地址慢也不无限拉长管理页请求（超预算地址按失败明示）
CALLBACK_PROBE_TOTAL_BUDGET_SECONDS = 30


def build_client_credentials() -> tuple[str, str, str, str]:
    """生成 client_id / client_secret 明文与落库哈希、前缀（复用 PAT 的 sha256 口径）。"""
    client_id = f"app_{secrets.token_hex(8)}"
    raw_secret = f"{CLIENT_SECRET_PREFIX}_{secrets.token_urlsafe(32)}"
    return client_id, raw_secret, hash_pat_token(raw_secret), raw_secret[:12]


def build_callback_secret() -> tuple[str, str]:
    """生成回调签名密钥（密文存储，明文只在创建/重置响应返回一次）。"""
    raw_secret = f"apc_{secrets.token_urlsafe(32)}"
    return raw_secret, encrypt_secret(raw_secret)


def verify_application_credentials(client_id: str, client_secret: str):
    """校验应用凭据（启用/过期/owner 启用），返回 ``(application, 错误文案)``。

    OAuth token/revoke 与 client-credentials 换发共用；哈希比较用 ``compare_digest``
    防时序侧信道（与 PAT 认证口径一致）。
    """
    application = ApiApplication.objects.filter(client_id=client_id).select_related("creator").first()
    if application is None or not hmac.compare_digest(application.client_secret_hash, hash_pat_token(client_secret)):
        return None, _("Invalid client credentials")
    now = timezone.now()
    if not application.is_active or (application.expired_at and application.expired_at <= now):
        return None, _("Application is disabled or expired")
    if application.creator is None or not application.creator.is_active:
        return None, _("Application owner account is disabled")
    return application, None


def send_test_callback(application: ApiApplication, url: str, client=None, timeout=None) -> dict:
    """向单个回调地址投递一次签名探测（返回值 = 投递结果，供管理页展示）。

    ``timeout`` 缺省用单地址预算（CALLBACK_TIMEOUT_SECONDS）；多地址串行探测时
    由调用方按剩余总预算传入更小值。

    生产路径（client=None）与 webhook 投递链路同口径走 ``pinned_request``：
    发送侧严格校验目标归属（私网/环回/link-local 拒绝，OUTBOUND_ALLOWED_HOSTS
    放行）并把连接固定为已校验 IP——裸 ``requests.post`` 对域名型回调存在 DNS
    rebinding 触达内网的 SSRF 窗口，且探测携带真实 HMAC 签名，绝不能打到内网。
    注入客户端路径（测试离线桩）保持原样调用，不做固定连接。
    """
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
    try:
        if client is None:
            from common.utils.outbound import pinned_request
            from task.services import outbound_allowed_hosts

            response = pinned_request(
                "POST",
                url,
                allow_private=False,
                allow_loopback=True,
                allowed_hosts=outbound_allowed_hosts(),
                data=body,
                headers=headers,
                timeout=timeout or CALLBACK_TIMEOUT_SECONDS,
            )
        else:
            response = client.post(url, data=body, headers=headers, timeout=timeout or CALLBACK_TIMEOUT_SECONDS)
        return {"url": url, "success": 200 <= response.status_code < 300, "status_code": response.status_code}
    except Exception as exc:  # noqa: BLE001 网络异常与拒绝同语义，按失败结果返回不打断管理页
        return {"url": url, "success": False, "detail": str(exc)}


def application_usage_stats(application: ApiApplication, days: int) -> dict:
    """应用用量报表（近 N 天）：聚合 OperationLog（token_pk ∈ 应用全部凭证）。

    凭证只失效不删除（删除应用才级联），故 token_pk 口径覆盖应用全生命周期；
    失败判定与个人令牌 stats 同源（业务码非成功即失败）；配额为软口径读取当日计数缓存。
    """
    from django.core.cache import cache
    from django.db.models import Avg, Count, Q
    from django.db.models.functions import TruncDate

    from audit.services import OperationLog

    since = timezone.now() - timedelta(days=days)
    token_pks = list(PersonalAccessToken.objects.filter(api_application=application).values_list("pk", flat=True))
    queryset = OperationLog.objects.filter(token_pk__in=token_pks, created_time__gte=since)
    failed_q = ~Q(status_code=API_SUCCESS_CODE)
    daily = [
        {
            "date": row["day"].isoformat() if row["day"] else "",
            "total": row["total"],
            "failed": row["failed"],
            "avg_duration": round(row["avg_duration"] or 0, 4),
        }
        for row in queryset.annotate(day=TruncDate("created_time"))
        .values("day")
        .annotate(total=Count("pk"), failed=Count("pk", filter=failed_q), avg_duration=Avg("exec_time"))
        .order_by("day")
    ]
    totals = queryset.aggregate(total=Count("pk"), failed=Count("pk", filter=failed_q), avg_duration=Avg("exec_time"))
    top_paths = list(queryset.values("path").annotate(total=Count("pk")).order_by("-total")[:10])
    status_codes = list(queryset.values("status_code").annotate(total=Count("pk")).order_by("-total")[:10])
    day_key = timezone.now().strftime("%Y%m%d")
    try:
        used_today = cache.get(f"api_app_quota_{application.pk}_{day_key}") or 0
    except Exception:  # noqa: BLE001 缓存故障按 0 展示（报表不阻断）
        used_today = 0
    return {
        "days": days,
        "total": totals["total"] or 0,
        "failed": totals["failed"] or 0,
        "avg_duration": round(totals["avg_duration"] or 0, 4),
        "daily": daily,
        "top_paths": top_paths,
        "status_codes": status_codes,
        "quota": {
            "daily_quota": application.daily_quota or 0,
            "alert_percent": application.quota_alert_percent or 80,
            "used_today": used_today,
        },
    }


class ApiApplicationTokenAPIView(APIView):
    """换发端点（client-credentials）：凭 client_id/client_secret 换 PAT 凭证。

    匿名可达（白名单 + AllowAny）：凭证即身份，与登录接口同口径。
    保留全局匿名限流（IP 维度），叠加 client 维度专用限流——
    client_secret 在线爆破 / 换发风暴（换发即轮换，频繁调用等于凭证写放大）。
    """

    authentication_classes: list[type] = []
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle, OpenClientThrottle]

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
        application, error = verify_application_credentials(client_id, client_secret)
        if application is None:
            return self._unauthorized(error)
        now = timezone.now()
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


class ApiApplicationFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = ApiApplication
        fields = ["is_active"]


class ApiApplicationViewSet(BaseModelSet):
    """API 应用（开放平台）"""

    queryset = ApiApplication.objects.all()
    serializer_class = ApiApplicationSerializer
    ordering_fields = ["created_time"]
    ordering = ["-created_time"]
    filterset_class = ApiApplicationFilter

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
            revoke_application_tokens(application)

    @action(methods=["post"], detail=True, url_path="regenerate-secret")
    def regenerate_secret(self, request, *args, **kwargs):
        """重置应用密钥：旧凭证与旧密钥即时失效，新密钥明文仅本次返回。"""
        application = self.get_object()
        client_id, raw_secret, secret_hash, secret_prefix = build_client_credentials()
        raw_callback_secret, callback_secret_encrypted = build_callback_secret()
        with transaction.atomic():
            revoke_application_tokens(application)
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
        # 总预算：多地址串行探测不因单个地址慢而无限拉长管理页请求；超预算的
        # 地址按失败明示（不静默跳过），前端逐条展示结果
        deadline = time.monotonic() + CALLBACK_PROBE_TOTAL_BUDGET_SECONDS
        results = []
        for url in urls:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                results.append({"url": url, "success": False, "detail": str(_("Probe budget exhausted"))})
                continue
            results.append(send_test_callback(application, url, timeout=min(CALLBACK_TIMEOUT_SECONDS, remaining)))
        return ApiResponse(data={"results": results})

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="scope-options")
    def scope_options(self, request, *args, **kwargs):
        """应用可授权的接口范围（按菜单分组，供应用「接口范围」勾选）

        口径与个人访问令牌同源（`identity/utils/pat_scope.py`）：权限菜单 × 请求用户角色
        （超管为全部启用的权限菜单）；条目是锚定正则（如 ``GET ^/api/system/user/?$``），
        只放行勾选的那一个接口。应用凭证以 owner（creator）身份走既有认证链，管理页由
        平台管理员维护，故选项集合取「当前用户可授权的接口」；非 owner 编辑时，超出
        选项的历史条目在前端自动落到「自定义」区，不会丢失。
        """
        return ApiResponse(data=scope_options_for_user(request.user))

    @action(methods=["get", "put"], detail=True, url_path="grants")
    def grants(self, request, *args, **kwargs):
        """应用资源授权规则（四级授权管理面）。

        GET：读取现有规则；PUT：全量替换（事务内按 pk 更新 / 缺失删除）。
        应用无规则 = 兼容模式（沿用一期 owner 权限 + scopes）；存在规则即白名单模式：
        模型/动作必须命中，字段与行级在覆盖规则上继续收敛（只收敛不提权）。
        """
        application = self.get_object()
        if request.method.lower() == "get":
            return ApiResponse(
                data={"results": ApiApplicationGrantSerializer(application.grants.all(), many=True).data}
            )
        payload = request.data.get("grants") if isinstance(request.data, dict) else request.data
        if not isinstance(payload, list):
            return ApiResponse(code=1001, detail=_("Grants must be a list"))
        with transaction.atomic():
            existing = {str(item.pk): item for item in application.grants.all()}
            kept = set()
            for item in payload:
                if not isinstance(item, dict):
                    return ApiResponse(code=1001, detail=_("Grants must be a list of objects"))
                instance = existing.get(str(item.get("pk") or ""))
                serializer = ApiApplicationGrantSerializer(instance, data=item, context=self.get_serializer_context())
                serializer.is_valid(raise_exception=True)
                serializer.save(application=application, creator=instance.creator if instance else request.user)
                kept.add(str(serializer.instance.pk))
            application.grants.exclude(pk__in=kept).delete()
        return ApiResponse(data={"results": ApiApplicationGrantSerializer(application.grants.all(), many=True).data})

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="stats")
    def stats(self, request, *args, **kwargs):
        """应用用量报表（近 N 天，默认 7 / 上限 30）。

        按天调用量、失败数、平均耗时 + Top 路径 + 业务码分布 + 当日配额用量
        （配额软口径：只告警不阻断）。
        """
        application = self.get_object()
        try:
            days = int(request.query_params.get("days") or 7)
        except (TypeError, ValueError):
            days = 7
        days = max(1, min(days, 30))
        return ApiResponse(data=application_usage_stats(application, days))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="grant-options")
    def grant_options(self, request, *args, **kwargs):
        """应用资源授权目录（模型 → 动作段 / 字段，粒度与「接口范围」同源）。

        只返回当前用户可授权面（超管为全部启用权限菜单 + 全部模型标签），不含业务
        数据行；路径登记白名单（表单枚举元数据口径，与 scope-options 同处理）。
        """
        return ApiResponse(data=grant_options_for_user(request.user))
