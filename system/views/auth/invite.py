#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : invite
"""邀请激活端点（匿名可达）。

- ``GET  /api/system/auth/invite/validate?token=``：令牌预检（激活页打开时调用，不消费令牌）；
- ``POST /api/system/auth/invite/accept``：校验一次性令牌并设置密码（激活即失效）。

限流复用 ``ResetPasswordThrottle``（同为匿名敏感端点，共享匿名限流窗口更严）。
密码按明文接收（前端邀请页不做 AES 二次加密）；生产应经 HTTPS 承载。
"""

from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.generics import GenericAPIView

from common.core.response import ApiResponse
from common.core.throttle import ResetPasswordThrottle
from common.swagger.utils import get_default_response_schema
from system.utils import user_invite


class InviteValidateAPIView(GenericAPIView):
    """邀请令牌预检"""

    permission_classes = []
    authentication_classes = []
    throttle_classes = [ResetPasswordThrottle]

    @extend_schema(
        parameters=[],
        responses=get_default_response_schema(),
    )
    def get(self, request, *args, **kwargs):
        token = str(request.query_params.get("token") or "").strip()
        user, state = user_invite.resolve_invite_token(token)
        return ApiResponse(data={"state": state, "username": getattr(user, "username", "")})


class InviteAcceptAPIView(GenericAPIView):
    """邀请激活：设置密码（令牌一次性）"""

    permission_classes = []
    authentication_classes = []
    throttle_classes = [ResetPasswordThrottle]

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "token": build_basic_type(OpenApiTypes.STR),
                    "password": build_basic_type(OpenApiTypes.STR),
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
        ok, detail = user_invite.accept_invite(user, password)
        if not ok:
            return ApiResponse(code=1003, detail=detail)
        # 令牌不显式删除：保留至自然过期，复用时会得到「已激活」的可读提示
        # （安全由 invite_status 状态机保证——非 pending 不可再激活）
        return ApiResponse(detail=detail)
