#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""Passkey（WebAuthn）凭据管理（F-9）。

流程：``challenge``（取服务器挑战值）→ 浏览器 ``navigator.credentials.create``
→ ``register``（提交 attestation 由服务端验签落库）；删除仅限本人凭据（超管可管理全部）。
"""

from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet

from common.core.modelset import BaseViewSet, DestroyAction, ListAction, SearchColumnsAction
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models import UserPasskey
from system.serializers.security import UserPasskeySerializer
from system.utils.webauthn import (
    SCENE_AUTHENTICATE,
    SCENE_REGISTER,
    b64url_encode,
    generate_challenge,
    rp_id_and_origin,
    verify_registration,
)

# 单用户凭据数量上限（多设备场景足够，防滥用）
MAX_PASSKEYS_PER_USER = 10


class PasskeyViewSet(BaseViewSet, ListAction, SearchColumnsAction, DestroyAction, GenericViewSet):
    """Passkey 凭据（仅本人；超管可见全部）"""

    queryset = UserPasskey.objects.select_related("user").all()
    serializer_class = UserPasskeySerializer
    ordering = ["-created_time"]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = getattr(self.request, "user", None)
        if user is None or getattr(user, "is_superuser", False):
            return queryset
        return queryset.filter(user=user)

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"scene": build_basic_type(OpenApiTypes.STR)},
                description="场景：register（绑定新凭据）/ authenticate（验证）",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="challenge")
    def challenge(self, request, *args, **kwargs):
        """获取 WebAuthn 挑战值（一次性，5 分钟有效）"""
        scene = str(request.data.get("scene") or SCENE_REGISTER)
        if scene not in (SCENE_REGISTER, SCENE_AUTHENTICATE):
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))
        rp_id, _origin = rp_id_and_origin(request)
        user = request.user
        return ApiResponse(
            data={
                "challenge": generate_challenge(user, scene),
                "rp_id": rp_id,
                "user_id": b64url_encode(str(user.pk).encode()),
                "username": user.username,
                "display_name": user.nickname or user.username,
            }
        )

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "client_data_json": build_basic_type(OpenApiTypes.STR),
                    "attestation_object": build_basic_type(OpenApiTypes.STR),
                    "name": build_basic_type(OpenApiTypes.STR),
                },
                required=["client_data_json", "attestation_object"],
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="register")
    def register(self, request, *args, **kwargs):
        """绑定一个 Passkey 凭据"""
        user = request.user
        if user.passkeys.count() >= MAX_PASSKEYS_PER_USER:
            return ApiResponse(
                code=1001, detail=_("The number of passkeys has reached the limit ({})").format(MAX_PASSKEYS_PER_USER)
            )
        rp_id, origin = rp_id_and_origin(request)
        try:
            data = verify_registration(user=user, payload=request.data, expected_rp_id=rp_id, expected_origin=origin)
        except ValueError as exc:
            return ApiResponse(code=1001, detail=str(exc))
        if UserPasskey.objects.filter(credential_id=data["credential_id"]).exists():
            return ApiResponse(code=1001, detail=_("This passkey has been bound"))
        passkey = UserPasskey.objects.create(user=user, creator=user, **data)
        return ApiResponse(data=self.get_serializer(passkey).data, detail=_("Passkey bound successfully"))
