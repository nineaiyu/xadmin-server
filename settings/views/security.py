#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : security
# author : ly_13
# date : 8/1/2024

from typing import Any

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.decorators import action

from captcha.contracts import (
    CaptchaInvalidError,
    preview_image_captcha,
    preview_interactive_challenge,
)
from common.core.response import ApiResponse
from common.utils import get_logger
from settings.serializers.security import (
    CaptchaImagePreviewResponseSerializer,
    CaptchaInteractivePreviewInSerializer,
    CaptchaInteractivePreviewResponseSerializer,
    SecurityBindEmailAuthSerializer,
    SecurityBindPhoneAuthSerializer,
    SecurityCaptchaCodeSerializer,
    SecurityCaptchaGeneralSerializer,
    SecurityCaptchaInteractiveSerializer,
    SecurityLoginAuthSerializer,
    SecurityLoginLimitSerializer,
    SecurityMFASerializer,
    SecurityMonitorSerializer,
    SecurityPasswordRuleSerializer,
    SecurityRegisterAuthSerializer,
    SecurityResetPasswordAuthSerializer,
    SecurityVerifyCodeSerializer,
)
from settings.services import build_setting_effective
from settings.views.settings import BaseSettingViewSet

logger = get_logger(__name__)


class SecurityPasswordRuleViewSet(BaseSettingViewSet):
    """密码规则"""

    serializer_class = SecurityPasswordRuleSerializer
    category = "security_password"


class SecurityLoginLimitViewSet(BaseSettingViewSet):
    """登录限制"""

    serializer_class = SecurityLoginLimitSerializer
    category = "security_login_limit"


class SecurityLoginAuthViewSet(BaseSettingViewSet):
    """登录安全"""

    serializer_class = SecurityLoginAuthSerializer
    category = "security_login_auth"

    def get_readonly_values(self, values: dict[str, Any]) -> dict[str, Any]:
        """附加「实际生效状态」诊断(与登录页能力清单同口径)。"""
        return {"effective": build_setting_effective("security_login_auth")}


class SecurityRegisterAuthViewSet(BaseSettingViewSet):
    """注册安全"""

    serializer_class = SecurityRegisterAuthSerializer
    category = "security_register_auth"

    def get_readonly_values(self, values: dict[str, Any]) -> dict[str, Any]:
        """附加「实际生效状态」诊断(与登录页能力清单同口径)。"""
        return {"effective": build_setting_effective("security_register_auth")}


class SecurityResetPasswordAuthViewSet(BaseSettingViewSet):
    """重置密码"""

    serializer_class = SecurityResetPasswordAuthSerializer
    category = "security_reset_password_auth"

    def get_readonly_values(self, values: dict[str, Any]) -> dict[str, Any]:
        """附加「实际生效状态」诊断(与登录页能力清单同口径)。"""
        return {"effective": build_setting_effective("security_reset_password_auth")}


class SecurityBindEmailAuthViewSet(BaseSettingViewSet):
    """绑定邮件"""

    serializer_class = SecurityBindEmailAuthSerializer
    category = "security_bind_email_auth"

    def get_readonly_values(self, values: dict[str, Any]) -> dict[str, Any]:
        """附加「实际生效状态」诊断(与登录页能力清单同口径)。"""
        return {"effective": build_setting_effective("security_bind_email_auth")}


class SecurityBindPhoneAuthViewSet(BaseSettingViewSet):
    """绑定手机"""

    serializer_class = SecurityBindPhoneAuthSerializer
    category = "security_bind_phone_auth"

    def get_readonly_values(self, values: dict[str, Any]) -> dict[str, Any]:
        """附加「实际生效状态」诊断(与登录页能力清单同口径)。"""
        return {"effective": build_setting_effective("security_bind_phone_auth")}


class SecurityVerifyCodeViewSet(BaseSettingViewSet):
    """验证码规则"""

    serializer_class = SecurityVerifyCodeSerializer
    category = "verify"


class SecurityCaptchaGeneralViewSet(BaseSettingViewSet):
    """验证码总配置"""

    serializer_class = SecurityCaptchaGeneralSerializer
    category = "captcha_general"


class SecurityCaptchaCodeViewSet(BaseSettingViewSet):
    """图片验证码"""

    serializer_class = SecurityCaptchaCodeSerializer
    category = "captcha"

    @extend_schema(
        request=None,
        responses={200: OpenApiResponse(response=CaptchaImagePreviewResponseSerializer)},
    )
    @action(detail=False, methods=["post"], url_path="preview")  # type: ignore[untyped-decorator]
    def preview(self, request: Any) -> Any:
        """预览图片验证码"""
        return ApiResponse(data=preview_image_captcha())


class SecurityCaptchaInteractiveViewSet(BaseSettingViewSet):
    """交互式验证码"""

    serializer_class = SecurityCaptchaInteractiveSerializer
    category = "captcha_interactive"

    @extend_schema(
        request=CaptchaInteractivePreviewInSerializer,
        responses={200: OpenApiResponse(response=CaptchaInteractivePreviewResponseSerializer)},
    )
    @action(detail=False, methods=["post"], url_path="preview")  # type: ignore[untyped-decorator]
    def preview(self, request: Any) -> Any:
        """预览交互式验证码"""
        serializer = CaptchaInteractivePreviewInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        captcha_type = str(serializer.validated_data.get("type") or "").strip()
        try:
            data = preview_interactive_challenge(captcha_type)
        except CaptchaInvalidError:
            return ApiResponse(
                code=1004,
                status=400,
                detail=_("Operation failed. Abnormal data"),
            )
        return ApiResponse(data=data)


class SecurityMFAViewSet(BaseSettingViewSet):
    """MFA 二次验证"""

    serializer_class = SecurityMFASerializer
    category = "security_mfa"


class SecurityMonitorViewSet(BaseSettingViewSet):
    """资源告警阈值"""

    serializer_class = SecurityMonitorSerializer
    category = "security_monitor"
