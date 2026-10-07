#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : invite
"""邀请激活端点（匿名可达）。

- ``GET  /api/system/auth/invite/validate?token=``：令牌预检（激活页打开时调用，不消费令牌），
  响应携带 ``encrypted`` 标志（取自 ``SECURITY_INVITE_ENCRYPTED_ENABLED``），前端据此
  决定是否把密码加密后再提交；
- ``POST /api/system/auth/invite/accept``：校验一次性令牌并设置密码（激活即失效）。

限流复用 ``ResetPasswordThrottle``（同为匿名敏感端点，共享匿名限流窗口更严）。
密码传输与注册 / 忘记密码重置同契约：加密开关开启时，前端以邀请令牌原文为密钥提交
AES 密文（``AesEncrypted(token, password)``，与 register/reset 同库同参序），服务端
``AESCipherV2(token)`` 解密；解密失败或明文为空按受控口径拒绝，不落 500，也不会把
密文误当密码交给强度校验。开关关闭时按明文接收。生产应经 HTTPS 承载。
"""

from django.conf import settings
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.generics import GenericAPIView

from common.base.utils import AESCipherV2
from common.core.response import ApiResponse
from common.core.throttle import ResetPasswordThrottle
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from identity.serializers.user import PASSWORD_DECRYPT_FAILED_MESSAGE
from identity.utils import user_invite

logger = get_logger(__name__)


class InviteValidateAPIView(GenericAPIView):
    """邀请令牌预检"""

    permission_classes: list[type] = []
    authentication_classes: list[type] = []
    throttle_classes = [ResetPasswordThrottle]

    @extend_schema(
        parameters=[],
        responses=get_default_response_schema(
            {
                "data": build_object_type(
                    properties={
                        "state": build_basic_type(OpenApiTypes.STR),
                        "username": build_basic_type(OpenApiTypes.STR),
                        "encrypted": build_basic_type(OpenApiTypes.BOOL),
                    }
                )
            }
        ),
    )
    def get(self, request, *args, **kwargs):
        token = str(request.query_params.get("token") or "").strip()
        user, state = user_invite.resolve_invite_token(token)
        return ApiResponse(
            data={
                "state": state,
                "username": getattr(user, "username", ""),
                "encrypted": settings.SECURITY_INVITE_ENCRYPTED_ENABLED,
            }
        )


class InviteAcceptAPIView(GenericAPIView):
    """邀请激活：设置密码（令牌一次性）"""

    permission_classes: list[type] = []
    authentication_classes: list[type] = []
    throttle_classes = [ResetPasswordThrottle]

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "token": build_basic_type(OpenApiTypes.STR),
                    "password": {
                        **build_basic_type(OpenApiTypes.STR),
                        "description": (
                            "Submit an AESCipherV2(token) ciphertext keyed by the invite token; "
                            "plaintext is only accepted when the encrypted switch is off"
                        ),
                    },
                }
            )
        ),
        responses=get_default_response_schema(),
    )
    def post(self, request, *args, **kwargs):
        token = str(request.data.get("token") or "").strip()
        password = str(request.data.get("password") or "")
        user, state = user_invite.resolve_invite_token(token)
        if state == "invalid":
            return ApiResponse(code=1001, detail=user_invite.INVITE_INVALID_MESSAGE)
        if state == "accepted":
            return ApiResponse(code=1002, detail=user_invite.INVITE_ACCEPTED_MESSAGE)
        # 令牌即密钥：校验通过后再解密（无效令牌拿不到可解的密文）。
        # 解密异常或明文为空 = 密钥不符/数据被篡改/明文误投，一律受控拒绝，
        # 不把原始提交值交给强度校验（与改密链路的受控解密同口径）
        if settings.SECURITY_INVITE_ENCRYPTED_ENABLED:
            try:
                password = AESCipherV2(token).decrypt(password)
            except Exception as e:
                logger.warning("invite accept password decrypt failed. user: %s, error: %s", user.username, e)
                password = ""
            if not password:
                return ApiResponse(code=1003, detail=PASSWORD_DECRYPT_FAILED_MESSAGE)
        ok, detail = user_invite.accept_invite(user, password)
        if not ok:
            return ApiResponse(code=1003, detail=detail)
        # 令牌不显式删除：保留至自然过期，复用时会得到「已激活」的可读提示
        # （安全由 invite_status 状态机保证——非 pending 不可再激活）
        return ApiResponse(detail=detail)
