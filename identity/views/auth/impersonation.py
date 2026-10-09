#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : impersonation
# author : ly_13
# date : 10/1/2026
from typing import Any

from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.generics import GenericAPIView

from common.core.permission import IsAuthenticated
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from identity.utils.impersonation import (
    blacklist_impersonated_refresh,
    is_impersonating,
    stop_impersonation,
)
from mfa.cache import UserConfirmStateCache

logger = get_logger(__name__)


class ImpersonateExitAPIView(GenericAPIView):
    """退出用户模拟（模拟态横幅点击退出即调用本接口）"""

    # 白名单 URL（PERMISSION_WHITE_URL）：被模拟用户未必有任何菜单权限，
    # 退出模拟是安全阀，必须无条件可达；视图内以 token claim ``imp`` 收口
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=OpenApiRequest(build_object_type(properties={"refresh": build_basic_type(OpenApiTypes.STR)})),
        responses=get_default_response_schema(),
    )
    def post(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """退出模拟：失效模拟态凭证，为模拟发起人重签 token"""
        if not is_impersonating(request):
            return ApiResponse(code=1001, detail=_("Not impersonating any user"))
        impersonator, data = stop_impersonation(request)
        if impersonator is None:
            # 发起人被停用/删除：无法恢复其登录态，只能重新登录（可读文案引导）
            logger.warning("impersonation exit failed: impersonator unavailable. user:%s", request.user)
            return ApiResponse(code=1001, detail=_("The original account is unavailable, please sign in again"))
        blacklist_impersonated_refresh(request)
        # 模拟期间积累的敏感操作确认状态属于被模拟用户，退出即清空
        UserConfirmStateCache(request.user).clear()
        return ApiResponse(data=data, detail=_("Exited impersonation of user {}").format(request.user.username))
