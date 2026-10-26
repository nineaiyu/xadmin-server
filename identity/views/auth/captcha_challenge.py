#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""交互式验证码匿名端点（挂 identity 的 no_auth_url）。

- ``POST /api/identity/auth/captcha/challenge``：获取挑战（不含答案）；
- ``POST /api/identity/auth/captcha/verify``：校验答案并签发一次性 ticket；
- ``GET  /api/identity/auth/captcha/inspect``：仅 ``CAPTCHA_INSPECT_ENABLED``
  为 True 时挂载（E2E 专用，生产默认配置不存在该路由）。
"""

from typing import Any

from django.conf import settings
from django.http import Http404
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.generics import GenericAPIView

from captcha.const import CAPTCHA_SCENE_LOGIN
from captcha.contracts import (
    CaptchaChallengeInSerializer,
    CaptchaChallengeResponseSerializer,
    CaptchaExhaustedError,
    CaptchaServiceError,
    CaptchaVerifyInSerializer,
    CaptchaVerifyResponseSerializer,
    InteractiveCaptcha,
    inspect_challenge,
)
from common.core.response import ApiResponse
from common.core.throttle import CaptchaChallengeThrottle, CaptchaVerifyThrottle
from common.utils.request import get_request_ip


def _service_error(exc: CaptchaServiceError) -> ApiResponse:
    detail = (
        _("Too many attempts or pending captcha tickets, please refresh")
        if isinstance(exc, CaptchaExhaustedError)
        else _("Captcha expired or invalid, please refresh")
    )
    # ``code`` 必须是数字（操作日志 status_code 为整型字段）；具体错误语义放
    # ``error`` 字符串，客户端可据此区分 invalid / exhausted。
    return ApiResponse(code=400, status=400, detail=detail, error=exc.code)


class CaptchaChallengeAPIView(GenericAPIView):
    """获取交互式验证码"""

    permission_classes: list[type] = []
    authentication_classes: list[type] = []
    throttle_classes = [CaptchaChallengeThrottle]
    serializer_class = CaptchaChallengeInSerializer

    @extend_schema(
        request=CaptchaChallengeInSerializer,
        responses={200: OpenApiResponse(response=CaptchaChallengeResponseSerializer)},
    )
    def post(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取交互式验证码挑战"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        scene = serializer.validated_data.get("scene", CAPTCHA_SCENE_LOGIN)
        try:
            data = InteractiveCaptcha().challenge(captcha_type=serializer.validated_data.get("type"), scene=scene)
        except CaptchaServiceError as exc:
            return _service_error(exc)
        return ApiResponse(data=data)


class CaptchaVerifyAPIView(GenericAPIView):
    """校验交互式验证码"""

    permission_classes: list[type] = []
    authentication_classes: list[type] = []
    throttle_classes = [CaptchaVerifyThrottle]
    serializer_class = CaptchaVerifyInSerializer

    @extend_schema(
        request=CaptchaVerifyInSerializer,
        responses={200: OpenApiResponse(response=CaptchaVerifyResponseSerializer)},
    )
    def post(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """校验答案并换取一次性 ticket"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            result = InteractiveCaptcha().verify(
                challenge_id=data["challenge_id"],
                captcha_type=data["type"],
                scene=data["scene"],
                answer=data.get("answer"),
                track=data.get("track"),
                ip=get_request_ip(request),
            )
        except CaptchaServiceError as exc:
            return _service_error(exc)
        return ApiResponse(data=result)


class CaptchaInspectAPIView(GenericAPIView):
    """检查验证码答案（仅 E2E）"""

    permission_classes: list[type] = []
    authentication_classes: list[type] = []
    throttle_classes = [CaptchaVerifyThrottle]

    @extend_schema(exclude=True)
    def get(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """读取挑战答案（仅 E2E 配置可达）"""
        if not getattr(settings, "CAPTCHA_INSPECT_ENABLED", False):
            raise Http404
        data = inspect_challenge(str(request.query_params.get("challenge_id") or ""))
        if not data:
            return ApiResponse(
                code=400,
                status=400,
                detail=_("Captcha expired or invalid, please refresh"),
                error="captcha_invalid",
            )
        return ApiResponse(data=data)
