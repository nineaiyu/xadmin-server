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

import time
from typing import Any

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

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet
from common.core.permission_meta import shared_list_action
from common.core.response import ApiResponse
from common.core.throttle import OpenClientThrottle
from common.swagger.utils import get_default_response_schema
from identity.models.token import ApiApplication
from identity.serializers.token import ApiApplicationGrantSerializer, ApiApplicationSerializer
from identity.services.open_platform import (  # noqa: F401 显式再导出：OAuth 端点仍按原路径导入
    CALLBACK_PROBE_TOTAL_BUDGET_SECONDS,
    CALLBACK_TIMEOUT_SECONDS,
    CLIENT_SECRET_PREFIX,
    application_usage_stats,
    send_test_callback,
)
from identity.services.open_platform import (
    build_callback_secret as build_callback_secret,
)
from identity.services.open_platform import (
    build_client_credentials as build_client_credentials,
)
from identity.services.open_platform import (
    verify_application_credentials as verify_application_credentials,
)
from identity.services.token_issue import issue_application_token, revoke_application_tokens
from identity.utils.api_grant import grant_options_for_user
from identity.utils.pat_scope import scope_options_for_user


class ApiApplicationTokenAPIView(APIView):
    """换发端点

    client-credentials：凭 client_id/client_secret 换 PAT 凭证

    匿名可达（白名单 + AllowAny）：凭证即身份，与登录接口同口径。
    保留全局匿名限流（IP 维度），叠加 client 维度专用限流——
    client_secret 在线爆破 / 换发风暴（换发即轮换，频繁调用等于凭证写放大）。
    """

    authentication_classes: list[type] = []
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle, OpenClientThrottle]

    @staticmethod
    def _unauthorized(detail: Any = None) -> Any:
        """凭据类失败一律 401（无认证类的视图抛 AuthenticationFailed 会被 DRF 归一为 403）。"""
        return ApiResponse(code=1001, detail=detail or _("Invalid client credentials"), status=401)

    def post(self, request: Any, *args: Any, **kwargs: Any) -> Any:
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

    def create(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """创建应用

        client_id / client_secret / callback_secret 由服务端生成，明文仅此一次
        """
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

    def perform_update(self, serializer: Any) -> None:
        """应用授权收紧即时生效：停用或 scopes/IP 白名单/有效期变更即失效全部凭证。"""
        instance = serializer.instance
        before = {field: getattr(instance, field, None) for field in ("scopes", "ip_allowlist", "expired_at")}
        application = serializer.save()
        changed = any(before[field] != getattr(application, field, None) for field in before)
        if not application.is_active or changed:
            revoke_application_tokens(application)

    @action(methods=["post"], detail=True, url_path="regenerate-secret")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def regenerate_secret(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """重置应用密钥

        旧凭证与旧密钥即时失效，新密钥明文仅本次返回
        """
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

    @action(methods=["post"], detail=True, url_path="test-callback")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def test_callback(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """向登记的回调地址逐一投递签名探测

        HMAC-SHA256 时间戳签名，同出站 webhook 口径
        """
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
    @shared_list_action(methods=["get"], detail=False, url_path="scope-options")  # type: ignore[untyped-decorator]
    def scope_options(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """应用可授权的接口范围

        按菜单分组，供应用「接口范围」勾选

        口径与个人访问令牌同源（`identity/utils/pat_scope.py`）：权限菜单 × 请求用户角色
        （超管为全部启用的权限菜单）；条目是锚定正则（如 ``GET ^/api/identity/user/?$``），
        只放行勾选的那一个接口。应用凭证以 owner（creator）身份走既有认证链，管理页由
        平台管理员维护，故选项集合取「当前用户可授权的接口」；非 owner 编辑时，超出
        选项的历史条目在前端自动落到「自定义」区，不会丢失。
        """
        return ApiResponse(data=scope_options_for_user(request.user))

    @action(methods=["get", "put"], detail=True, url_path="grants")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def grants(self, request: Any, *args: Any, **kwargs: Any) -> Any:
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
    @action(methods=["get"], detail=True, url_path="stats")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def stats(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """应用用量报表

        近 N 天，默认 7 / 上限 30

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
    @shared_list_action(methods=["get"], detail=False, url_path="grant-options")  # type: ignore[untyped-decorator]
    def grant_options(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """应用资源授权目录

        模型 → 动作段 / 字段，粒度与「接口范围」同源

        只返回当前用户可授权面（超管为全部启用权限菜单 + 全部模型标签），不含业务
        数据行；路径登记白名单（表单枚举元数据口径，与 scope-options 同处理）。
        """
        return ApiResponse(data=grant_options_for_user(request.user))
