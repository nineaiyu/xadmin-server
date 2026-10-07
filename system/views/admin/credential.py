#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""凭据与密钥：只读聚合 + 轮换。

- ``overview``：Setting 加密项 / SystemConfig 敏感键 / 模型字段级凭据的状态清单
  （名称 / 类型 / 存储形态 / 加密状态 / 是否可原地轮换 / 更换入口 / 最近更新时间）；
  **不回传任何值**（密文与明文都不回传，避免二次泄露面）；
- ``rotate``：仅对「服务端自生成」的凭据做原地轮换（重新生成随机值并加密落库）；
  外部签发的凭据拒绝轮换、返回更换入口，避免造假值打挂集成。高危动作由前端二次
  确认 + 审计留痕（module=system:credential）。
"""

from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.viewsets import GenericViewSet

from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models import SystemConfig
from system.utils.platform.credential import (
    credential_overview,
    regenerate_system_config,
    rotate_model_field,
)

# rotate 的合法 scope 枚举：Setting 体系凭据均为外部签发、不提供原地轮换，
# 不在轮换入参面内（总览只给更换入口）
ROTATE_SCOPES = ("model_field", "system_config")


class CredentialViewSet(GenericViewSet):
    """凭据与密钥"""

    queryset = SystemConfig.objects.none()

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="overview")
    def overview(self, request, *args, **kwargs):
        """获取凭据清单"""
        return ApiResponse(data=credential_overview())

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                required=["key"],
                properties={
                    "key": build_basic_type(OpenApiTypes.STR),
                    "scope": build_basic_type(OpenApiTypes.STR),
                },
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="rotate")
    def rotate(self, request, *args, **kwargs):
        """轮换凭据（重新生成随机值并加密落库）"""
        key = str(request.data.get("key") or "").strip()
        scope = str(request.data.get("scope") or "system_config")
        if not key:
            return ApiResponse(code=1001, detail=_("A credential key is required"))
        # scope 显式收口为合法枚举：未知值直接 400，不落入「Setting 不可轮换」
        # 的业务分支伪装语义（Setting 凭据根本没有轮换入口）
        if scope not in ROTATE_SCOPES:
            raise ValidationError(_("Rotation scope must be 'model_field' or 'system_config'"))
        if scope == "model_field":
            result = rotate_model_field(key, user=request.user)
        else:
            result = regenerate_system_config(key, user=request.user)
        if not result.get("ok"):
            return ApiResponse(code=1001, detail=result.get("detail") or _("Credential rotation failed"))
        if result.get("action") == "skip":
            return ApiResponse(data={"action": "skip"}, detail=result.get("detail"))
        return ApiResponse(data={"action": result.get("action")}, detail=_("Credential rotated"))
