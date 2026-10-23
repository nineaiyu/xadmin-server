#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""已授权应用：个人视角与管理员审计，以及按应用的授权撤销。

聚合口径：
- OAuth 授权码：`OAuthRefreshToken`（未撤销、未过期）→ 每个应用一行；
- 应用换发的访问凭证：`PersonalAccessToken`（name 前缀 ``oauth:``，属主为授权用户）；
- 撤销只动 OAuth 授权链（refresh + 关联 access + ``oauth:`` PAT），
  不影响应用 owner 的 client-credentials 凭证（name 前缀 ``app:``）。
"""

from typing import Any

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet

from common.core.modelset import ListAction
from common.core.modelset.base import BaseViewSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from identity.models import ApiApplication, OAuthRefreshToken, PersonalAccessToken
from identity.serializers.authorized_app import AuthorizedApplicationSerializer

OAUTH_ACCESS_PREFIX = "oauth:"


def _application_status(application: ApiApplication) -> str:
    now = timezone.now()
    if not application.is_active:
        return "disabled"
    if application.expired_at and application.expired_at <= now:
        return "expired"
    return "active"


def authorized_rows(user: Any = None) -> list[dict[str, Any]]:
    """按应用聚合授权行；``user`` 非空时只取该用户的授权。"""
    refresh_qs = OAuthRefreshToken.objects.filter(is_revoked=False).select_related(
        "application", "user", "access_token"
    )
    refresh_qs = refresh_qs.filter(Q(expired_at__isnull=True) | Q(expired_at__gt=timezone.now()))
    pat_qs = PersonalAccessToken.objects.filter(
        api_application__isnull=False, is_active=True, name__startswith=OAUTH_ACCESS_PREFIX
    ).select_related("api_application", "creator")
    if user is not None:
        refresh_qs = refresh_qs.filter(user=user)
        pat_qs = pat_qs.filter(creator=user)

    rows: dict[str, dict[str, Any]] = {}

    def ensure(application: ApiApplication) -> dict[str, Any]:
        row = rows.get(str(application.pk))
        if row is None:
            row = {
                "application": {
                    "pk": str(application.pk),
                    "name": application.name,
                    "client_id": application.client_id,
                },
                "users": {},
                "scopes": set(),
                "authorized_time": None,
                "last_used_time": None,
                "access_token_count": 0,
                "refresh_token_count": 0,
                "status": _application_status(application),
            }
            rows[str(application.pk)] = row
        return row

    for token in refresh_qs:
        row = ensure(token.application)
        row["refresh_token_count"] += 1
        row["scopes"].update(token.scopes or [])
        created = token.created_time
        if created and (row["authorized_time"] is None or created < row["authorized_time"]):
            row["authorized_time"] = created
        last_used = getattr(token.access_token, "last_used_time", None) if token.access_token else None
        if last_used and (row["last_used_time"] is None or last_used > row["last_used_time"]):
            row["last_used_time"] = last_used
        row["users"][str(token.user_id)] = {
            "pk": str(token.user_id),
            "username": token.user.username,
            "nickname": token.user.nickname,
        }

    for token in pat_qs:
        row = ensure(token.api_application)
        row["access_token_count"] += 1
        row["scopes"].update(token.scopes or [])
        created = token.created_time
        if created and (row["authorized_time"] is None or created < row["authorized_time"]):
            row["authorized_time"] = created
        if token.last_used_time and (row["last_used_time"] is None or token.last_used_time > row["last_used_time"]):
            row["last_used_time"] = token.last_used_time
        row["users"][str(token.creator_id)] = {
            "pk": str(token.creator_id),
            "username": token.creator.username,
            "nickname": token.creator.nickname,
        }

    result = []
    for row in rows.values():
        users = list(row["users"].values())
        result.append(
            {
                **row,
                "users": users,
                "user_count": len(users),
                "scopes": sorted(row["scopes"]),
            }
        )
    return sorted(result, key=lambda item: item["authorized_time"] or timezone.now(), reverse=True)


def revoke_application_authorizations(application: ApiApplication, user: Any = None) -> dict[str, int]:
    """撤销某应用的 OAuth 授权；``user`` 非空时只动该用户。"""
    refresh_qs = OAuthRefreshToken.objects.filter(application=application, is_revoked=False)
    if user is not None:
        refresh_qs = refresh_qs.filter(user=user)
    access_ids = list(refresh_qs.exclude(access_token__isnull=True).values_list("access_token_id", flat=True))
    refresh_count = refresh_qs.update(is_revoked=True)
    pat_qs = PersonalAccessToken.objects.filter(
        api_application=application, is_active=True, name__startswith=OAUTH_ACCESS_PREFIX
    )
    if user is not None:
        pat_qs = pat_qs.filter(creator=user)
    access_count = pat_qs.update(is_active=False)
    if access_ids:
        PersonalAccessToken.objects.filter(pk__in=access_ids, is_active=True).update(is_active=False)
    return {"refresh_tokens": refresh_count, "access_tokens": access_count}


class AuthorizedApplicationViewSet(BaseViewSet, ListAction, GenericViewSet):
    """已授权应用（管理员审计）"""

    serializer_class = AuthorizedApplicationSerializer
    queryset = ApiApplication.objects.none()

    def list(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取已授权应用列表"""
        rows = authorized_rows()
        return ApiResponse(data={"total": len(rows), "results": rows})

    @extend_schema(request=None, responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="revoke")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def revoke(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """撤销应用全部授权"""
        application = ApiApplication.objects.filter(pk=kwargs.get("pk")).first()
        if application is None:
            return ApiResponse(code=1001, detail=_("Application not found"))
        result = revoke_application_authorizations(application)
        return ApiResponse(detail=_("Authorizations revoked"), data=result)


class MyAuthorizedApplicationViewSet(BaseViewSet, ListAction, GenericViewSet):
    """我的已授权应用"""

    serializer_class = AuthorizedApplicationSerializer
    queryset = ApiApplication.objects.none()

    def list(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取我的已授权应用"""
        rows = authorized_rows(user=request.user)
        return ApiResponse(data={"total": len(rows), "results": rows})

    @extend_schema(request=None, responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="revoke")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def revoke(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """撤销我的应用授权"""
        application = ApiApplication.objects.filter(pk=kwargs.get("pk")).first()
        if application is None:
            return ApiResponse(code=1001, detail=_("Application not found"))
        result = revoke_application_authorizations(application, user=request.user)
        return ApiResponse(detail=_("Authorizations revoked"), data=result)
