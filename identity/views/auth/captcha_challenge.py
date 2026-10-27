#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""交互式验证码匿名端点（挂 identity 的 no_auth_url）。

- ``POST /api/identity/auth/captcha/challenge``：获取挑战（不含答案）；
- ``POST /api/identity/auth/captcha/verify``：校验答案并签发一次性 ticket；
- ``GET  /api/identity/auth/captcha/audio/<challenge_id>``：图片题音频（无障碍）。
"""

import secrets
from typing import Any

from django.conf import settings
from django.http import Http404
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.generics import GenericAPIView

from captcha import telemetry
from captcha.audio import synthesize_audio
from captcha.const import CAPTCHA_SCENE_LOGIN
from captcha.contracts import (
    CaptchaChallengeInSerializer,
    CaptchaChallengeResponseSerializer,
    CaptchaServiceError,
    CaptchaVerifyInSerializer,
    CaptchaVerifyResponseSerializer,
    InteractiveCaptcha,
    inspect_challenge,
    xff_inconsistent,
)
from captcha.http import audio_file_response
from common.core.response import ApiResponse
from common.core.throttle import CaptchaAudioThrottle, CaptchaChallengeThrottle, CaptchaVerifyThrottle
from common.utils.request import get_request_ip

CAPTCHA_SID_COOKIE = "captcha_sid"
CAPTCHA_SID_PATH = "/api/identity"


def _service_error(exc: CaptchaServiceError) -> ApiResponse:
    # 所有失败（含耗尽 / deny / 签名错）同形，避免成为攻击者的探针。
    return ApiResponse(
        code=400,
        status=400,
        detail=_("Captcha expired or invalid, please refresh"),
        error="captcha_invalid",
    )


def _captcha_sid(request: Any) -> str:
    return str(getattr(request, "COOKIES", {}).get(CAPTCHA_SID_COOKIE) or "")


def _origin_allowed(request: Any) -> bool:
    """浏览器请求的 Origin 校验；无 Origin 的 API 客户端保持兼容。"""
    if not bool(getattr(settings, "CAPTCHA_ORIGIN_CHECK_ENABLED", True)):
        return True
    origin = str(request.headers.get("Origin") or "").strip().rstrip("/")
    if not origin:
        return True
    # 同源/同站浏览器请求：反代后 Host 可能变化，以 Fetch Metadata 兜底
    fetch_site = str(request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if fetch_site in {"same-origin", "same-site"}:
        return True
    allowed = {
        str(item or "").strip().rstrip("/")
        for item in (
            *str(getattr(settings, "CAPTCHA_ALLOWED_ORIGINS", "") or "").split(","),
            *(getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or []),
            *(getattr(settings, "CORS_ALLOWED_ORIGINS", []) or []),
            str(getattr(settings, "SITE_URL", "") or ""),
            f"{request.scheme}://{request.get_host()}",
        )
        if str(item or "").strip()
    }
    return origin in allowed


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
        if not _origin_allowed(request):
            return _service_error(CaptchaServiceError())
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        scene = serializer.validated_data.get("scene", CAPTCHA_SCENE_LOGIN)
        # 同一请求内的事件合并为一次投递（异常路径同样冲刷）
        telemetry.begin_request()
        try:
            data = InteractiveCaptcha().challenge(
                captcha_type=serializer.validated_data.get("type"),
                scene=scene,
                action=str(serializer.validated_data.get("action") or ""),
                env=serializer.validated_data.get("env"),
                ip=get_request_ip(request),
                ua=str(request.META.get("HTTP_USER_AGENT") or ""),
                accept_language=str(request.META.get("HTTP_ACCEPT_LANGUAGE") or ""),
                sec_ch_ua=str(request.META.get("HTTP_SEC_CH_UA") or ""),
                account=str(serializer.validated_data.get("account") or ""),
                honeypot=bool(serializer.validated_data.get("hp")),
                xff_inconsistent=xff_inconsistent(request.META),
                sid=_captcha_sid(request),
            )
        except CaptchaServiceError as exc:
            return _service_error(exc)
        finally:
            telemetry.end_request()
        sid = str(data.pop("_sid", "") or "")
        response = ApiResponse(data=data)
        if sid:
            # cookie 必须覆盖「挑战有效期 + 票据有效期」：用户拖到挑战快过期才作答时，
            # 登录消费票据仍要带得上 sid（只按挑战 TTL 会让 cookie 先于票据失效）
            cookie_max_age = int(data.get("ttl") or 120) + int(getattr(settings, "CAPTCHA_TICKET_TTL", 120) or 120) + 60
            response.set_cookie(
                CAPTCHA_SID_COOKIE,
                sid,
                max_age=cookie_max_age,
                httponly=True,
                secure=request.is_secure(),
                samesite="Lax",
                path=CAPTCHA_SID_PATH,
            )
        return response


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
        if not _origin_allowed(request):
            return _service_error(CaptchaServiceError())
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        telemetry.begin_request()
        try:
            result = InteractiveCaptcha().verify(
                challenge_id=data["challenge_id"],
                captcha_type=data["type"],
                scene=data["scene"],
                answer=data.get("answer"),
                track=data.get("track"),
                ip=get_request_ip(request),
                action=str(data.get("action") or ""),
                gen_time=data.get("gen_time"),
                nonce=data.get("nonce"),
                sign=data.get("sign"),
                ua=str(request.META.get("HTTP_USER_AGENT") or ""),
                env=data.get("env"),
                pow_answer=data.get("pow_answer"),
                input_mode=str(data.get("input") or ""),
                honeypot=bool(data.get("hp")),
                account=str(data.get("account") or ""),
                accept_language=str(request.META.get("HTTP_ACCEPT_LANGUAGE") or ""),
                sec_ch_ua=str(request.META.get("HTTP_SEC_CH_UA") or ""),
                xff_inconsistent=xff_inconsistent(request.META),
                sid=_captcha_sid(request),
            )
        except CaptchaServiceError as exc:
            return _service_error(exc)
        finally:
            telemetry.end_request()
        return ApiResponse(data=result)


class CaptchaInspectAPIView(GenericAPIView):
    """受控答案检查

    仅 E2E 开启 ``CAPTCHA_INSPECT_ENABLED`` 且请求头匹配专用 token 时可达。
    """

    permission_classes: list[type] = []
    authentication_classes: list[type] = []
    throttle_classes = [CaptchaVerifyThrottle]

    @extend_schema(exclude=True)
    def get(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """读取挑战答案"""
        expected = str(getattr(settings, "CAPTCHA_INSPECT_TOKEN", "") or "")
        provided = str(request.headers.get("X-Captcha-Inspect-Token") or "")
        if (
            not getattr(settings, "CAPTCHA_INSPECT_ENABLED", False)
            or not expected
            or not secrets.compare_digest(provided, expected)
        ):
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


class CaptchaAudioAPIView(GenericAPIView):
    """图片验证码音频（无障碍回退）"""

    permission_classes: list[type] = []
    authentication_classes: list[type] = []
    throttle_classes = [CaptchaAudioThrottle]

    @extend_schema(exclude=True)
    def get(self, request: Any, challenge_id: str, *args: Any, **kwargs: Any) -> Any:
        """播放图片挑战的音频"""
        try:
            text = InteractiveCaptcha().audio(challenge_id, sid=_captcha_sid(request))
        except CaptchaServiceError as exc:
            raise Http404 from exc
        from captcha import signals as captcha_signals

        captcha_signals.record_audio(get_request_ip(request))
        path = synthesize_audio(text, challenge_id)
        if not path:
            raise Http404
        return audio_file_response(request, path, f"{challenge_id}.wav")
