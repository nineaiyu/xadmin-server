#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SCIM 目录同步设置页：配置自检、令牌生成/轮换与最近同步记录。"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from common.core.config import SysConfig
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from settings.serializers.scim import RATE_LIMIT_RE, ScimSettingSerializer
from settings.views.system_config import SystemConfigSettingViewSet
from system.services.credential_rotate import issue_system_config_secret

SCIM_SYNC_STATUS_LIMIT = 20


class ScimSettingViewSet(SystemConfigSettingViewSet):
    """SCIM 目录同步设置"""

    serializer_class = ScimSettingSerializer
    config_keys = ("SCIM_ENABLED", "SCIM_TOKEN", "SCIM_RATE_LIMIT", "SCIM_DEFAULT_ROLE_CODE")

    def get_values(self) -> dict[str, Any]:
        values = super().get_values()
        if values.get("SCIM_TOKEN"):
            values["SCIM_TOKEN"] = ""
        return values

    def get_readonly_values(self, values: dict[str, Any]) -> dict[str, Any]:
        return {"SCIM_TOKEN_CONFIGURED": bool(SysConfig.SCIM_TOKEN)}

    def create(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """校验 SCIM 配置

        只做本地自检：开关 / 令牌 / 限流格式 / 默认角色是否可用；
        SCIM 由 IdP 主动回调，平台侧没有可拨测的对端连接。
        """

        # 自检读库内最新值：多 worker 下 L1 缓存最长 30s 才收敛，
        # 保存后立即自检不应命中其他 worker 的旧值。
        def fresh(key: str, default: Any = None) -> Any:
            data = SysConfig.get_value_from_db(key)
            return data.get("value", default) if data else default

        enabled = bool(fresh("SCIM_ENABLED", False))
        token_configured = bool(fresh("SCIM_TOKEN", ""))
        rate_limit = str(fresh("SCIM_RATE_LIMIT", "") or "")
        role_code = str(fresh("SCIM_DEFAULT_ROLE_CODE", "") or "")
        problems = []
        if not enabled:
            problems.append(str(_("SCIM is disabled")))
        if not token_configured:
            problems.append(str(_("SCIM token is not configured")))
        if rate_limit and rate_limit != "0" and not RATE_LIMIT_RE.match(rate_limit):
            problems.append(str(_("Invalid SCIM rate limit: {}").format(rate_limit)))
        if role_code:
            from identity.models import UserRole

            if not UserRole.objects.filter(code=role_code, deleted_at__isnull=True).exists():
                problems.append(str(_("Unknown role code: {}").format(role_code)))
        data = {
            "enabled": enabled,
            "token_configured": token_configured,
            "rate_limit": rate_limit,
            "default_role_code": role_code,
            "endpoint": "/api/scim/v2",
        }
        if problems:
            return ApiResponse(code=1001, detail="; ".join(problems), data=data)
        return ApiResponse(detail=_("SCIM configuration is ready"), data=data)

    @extend_schema(request=None, responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="rotate-token")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def rotate_token(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """生成或轮换 SCIM 令牌

        Returns the plaintext token once; the previous token becomes invalid immediately.
        """
        result = issue_system_config_secret("SCIM_TOKEN", user=request.user)
        if not result.get("ok"):
            return ApiResponse(code=1001, detail=result.get("detail") or _("SCIM token rotation failed"))
        return ApiResponse(
            detail=_("SCIM token rotated; copy it now because it will not be shown again"),
            data={"token": result.get("value")},
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="sync-status")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def sync_status(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """SCIM 同步记录

        最近的 IdP 写入审计（OperationLog.auth_type=scim），不含请求体。
        """
        from audit.services import OperationLog

        queryset = OperationLog.objects.filter(auth_type=OperationLog.AuthType.SCIM).order_by("-created_time")
        total = queryset.count()
        rows = [
            {
                "created_time": row["created_time"],
                "module": row["module"],
                "path": row["path"],
                "method": row["method"],
                "object_pk": row["object_pk"],
                "status_code": row["status_code"],
                "ipaddress": row["ipaddress"],
            }
            for row in queryset.values(
                "created_time", "module", "path", "method", "object_pk", "status_code", "ipaddress"
            )[:SCIM_SYNC_STATUS_LIMIT]
        ]
        return ApiResponse(data={"total": total, "results": rows})
