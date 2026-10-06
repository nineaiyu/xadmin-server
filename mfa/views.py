#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : views
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet

from common.core.permission import IsAuthenticated
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from common.utils.request import get_request_ip
from mfa import recovery
from mfa.backends import MFA_BACKEND_CLASSES, get_enabled_backends
from mfa.backends.otp import OtpBackend
from mfa.cache import OtpBindCache, UserConfirmStateCache
from mfa.confirm import UserConfirmation
from mfa.const import CONFIRM_TYPE_TTL_SETTING, ConfirmType
from mfa.serializers import ConfirmSerializer, OtpBindConfirmSerializer, SendCodeSerializer
from mfa.services import (
    get_confirm_methods,
    is_method_binding_allowed,
    send_user_mfa_code,
    verify_user_confirm,
)
from settings.services import MFABlockUtils

logger = get_logger(__name__)


def _get_confirm_type(value):
    return value if value in ConfirmType.values else ConfirmType.MFA


def _binding_disallowed(user, backend_name):
    """绑定入口与验证同口径：该方式不在账号方式白名单（交集）内则拒绝绑定。

    验证侧 ``get_enabled_backends`` 本就按白名单过滤，但绑定入口此前不校验——
    共享账号（如公开演示账号）被他人绑定 MFA 后，策略强制二次验证会锁死共享登录。
    判定实现在 mfa.services.is_method_binding_allowed（契约层）。
    """
    return not is_method_binding_allowed(user, backend_name)


def _missing_backup_channel(user) -> bool:
    """绑定 OTP 要求至少一个备用挑战渠道（短信/邮件）。

    只约束用户层：部署侧没有任何可用挑战渠道（后端未启用或 EMAIL_ENABLED /
    SMS_ENABLED 关闭）时不拦截——纯 OTP 部署以恢复码为唯一自救通道，强制只会
    让 MFA 无法开启；存在可用渠道而用户一个都没配置时拒绝，避免设备全丢后
    除管理员 reset 外无任何入口。Passkey 属持有型因素而非挑战渠道，不计入。
    """
    challenge_backends = [cls for cls in MFA_BACKEND_CLASSES if cls.challenge_required]
    if not any(cls.global_enabled() for cls in challenge_backends):
        return False
    user_channels = {b.name for b in get_enabled_backends(user) if b.challenge_required}
    return not user_channels


def _state_expire_at(state):
    if not state:
        return None
    ttl = int(getattr(settings, CONFIRM_TYPE_TTL_SETTING[state["type"]]))
    return int(state["time"] + ttl)


class UserConfirmViewSet(GenericViewSet):
    """敏感操作二次验证

    前端交互流程：请求敏感 API 收到 412（type=user_confirm_required）后，
    1. GET  /api/mfa/confirm?confirm_type=mfa      获取可用验证方式渲染验证弹窗；
    2. POST /api/mfa/confirm/send-code             挑战型方式（短信/邮件）先下发验证码；
    3. POST /api/mfa/confirm                       提交验证，通过后有效期内免重复验证。
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[{"name": "confirm_type", "in": "query", "schema": {"type": "string", "enum": ConfirmType.values}}],
        responses=get_default_response_schema(),
    )
    def retrieve(self, request, *args, **kwargs):
        """获取可用验证方式与当前确认状态"""
        confirm_type = _get_confirm_type(request.query_params.get("confirm_type"))
        state_cache = UserConfirmStateCache(request.user)
        state = state_cache.get()
        return ApiResponse(
            data={
                "confirm_type": confirm_type,
                "methods": get_confirm_methods(request.user, request, confirm_type),
                "confirmed": state_cache.is_valid_for(confirm_type),
                "expire_at": _state_expire_at(state),
            }
        )

    @extend_schema(request=ConfirmSerializer, responses=get_default_response_schema())
    def create(self, request, *args, **kwargs):
        """提交验证：校验通过后写入确认状态"""
        serializer = ConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        confirm_type = serializer.validated_data["confirm_type"]
        ok, msg = verify_user_confirm(
            request.user,
            serializer.validated_data["method"],
            serializer.validated_data["code"],
            request=request,
            confirm_type=confirm_type,
        )
        if not ok:
            return ApiResponse(code=1002, detail=msg)
        return ApiResponse(
            data={"expire_at": _state_expire_at(UserConfirmStateCache(request.user).get())},
            detail=_("Verification successful"),
        )

    @extend_schema(request=SendCodeSerializer, responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="send-code", serializer_class=SendCodeSerializer)
    def send_code(self, request, *args, **kwargs):
        """发送挑战验证码（短信/邮件）"""
        serializer = SendCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ok, msg = send_user_mfa_code(request.user, serializer.validated_data["method"], request=request)
        if not ok:
            return ApiResponse(code=1002, detail=msg)
        return ApiResponse(detail=_("The verification code has been sent"))


class UserOTPViewSet(GenericViewSet):
    """个人 OTP(TOTP) 绑定管理"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=get_default_response_schema())
    def retrieve(self, request, *args, **kwargs):
        """获取绑定状态"""
        return ApiResponse(
            data={
                "enabled": request.user.mfa_enabled,
                "bound": bool(request.user.otp_secret_key),
                "phone": request.user.phone,
                "email": request.user.email,
            }
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="start")
    def start(self, request, *args, **kwargs):
        """发起绑定：生成候选密钥与 otpauth URI（前端渲染二维码）"""
        if _binding_disallowed(request.user, OtpBackend.name):
            return ApiResponse(code=1001, detail=_("MFA method is not allowed by account policy"))
        if request.user.mfa_enabled:
            return ApiResponse(code=1001, detail=_("OTP is already bound"))
        if _missing_backup_channel(request.user):
            return ApiResponse(
                code=1001, detail=_("Bind at least one backup challenge channel (email or SMS) before enabling OTP")
            )
        bind_cache = OtpBindCache(request.user)
        secret = bind_cache.get_secret()
        if not secret:
            secret = OtpBackend.generate_secret()
            bind_cache.set_secret(secret)
        return ApiResponse(data={"secret": secret, "uri": OtpBackend.get_provisioning_uri(request.user, secret)})

    @extend_schema(request=OtpBindConfirmSerializer, responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="confirm", serializer_class=OtpBindConfirmSerializer)
    def confirm(self, request, *args, **kwargs):
        """确认绑定：校验动态码后写入密钥，并自动开启登录 MFA

        绑定成功同时生成一批恢复码（``data.recovery_codes``，明文仅此一次展示）。
        """
        user = request.user
        if _binding_disallowed(user, OtpBackend.name):
            return ApiResponse(code=1001, detail=_("MFA method is not allowed by account policy"))
        if user.mfa_enabled:
            return ApiResponse(code=1001, detail=_("OTP is already bound"))
        if _missing_backup_channel(user):
            return ApiResponse(
                code=1001, detail=_("Bind at least one backup challenge channel (email or SMS) before enabling OTP")
            )
        secret = OtpBindCache(user).get_secret()
        if not secret:
            return ApiResponse(code=1001, detail=_("Please start binding first"))

        serializer = OtpBindConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        block = MFABlockUtils(user.username, get_request_ip(request))
        if block.is_block():
            return ApiResponse(code=1001, detail=_("Too many failures, the account has been locked"))
        if not OtpBackend.verify_code(secret, serializer.validated_data["code"]):
            block.incr_failed_count()
            return ApiResponse(code=1002, detail=_("The OTP verification code is incorrect"))
        block.clean_failed_count()

        user.otp_secret_key = secret
        user.mfa_level = get_user_model().MFALevelChoices.ENABLED
        user.save(update_fields=["otp_secret_key", "mfa_level"])
        OtpBindCache(user).clear()
        codes = recovery.generate_codes(user)
        return ApiResponse(data={"recovery_codes": codes}, detail=_("OTP binding successful"))

    @extend_schema(responses=get_default_response_schema())
    @action(
        methods=["post"],
        detail=False,
        url_path="close",
        permission_classes=[IsAuthenticated, UserConfirmation.require(ConfirmType.PASSWORD)],
    )
    def close(self, request, *args, **kwargs):
        """关闭登录二次验证（敏感操作：需先通过二次验证，未验证时返回 412）。

        仅停用开关，保留已绑定的密钥，重新开启时校验动态码即可，无需重新扫码。
        """
        user = request.user
        if not user.otp_secret_key:
            return ApiResponse(code=1001, detail=_("OTP is not bound"))
        user.mfa_level = get_user_model().MFALevelChoices.DISABLED
        user.save(update_fields=["mfa_level"])
        return ApiResponse(detail=_("Login MFA disabled"))

    @extend_schema(request=OtpBindConfirmSerializer, responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="open", serializer_class=OtpBindConfirmSerializer)
    def open(self, request, *args, **kwargs):
        """重新开启登录二次验证（密钥保留时校验一次动态码证明持有，无需重新扫码）"""
        user = request.user
        if not user.otp_secret_key:
            return ApiResponse(code=1001, detail=_("Please start binding first"))

        serializer = OtpBindConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        block = MFABlockUtils(user.username, get_request_ip(request))
        if block.is_block():
            return ApiResponse(code=1001, detail=_("Too many failures, the account has been locked"))
        if not OtpBackend.verify_code(user.otp_secret_key, serializer.validated_data["code"]):
            block.incr_failed_count()
            return ApiResponse(code=1002, detail=_("The OTP verification code is incorrect"))
        block.clean_failed_count()

        user.mfa_level = get_user_model().MFALevelChoices.ENABLED
        user.save(update_fields=["mfa_level"])
        return ApiResponse(detail=_("Login MFA enabled"))

    @extend_schema(request=OtpBindConfirmSerializer, responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="test", serializer_class=OtpBindConfirmSerializer)
    def test(self, request, *args, **kwargs):
        """校验已绑定密钥的动态码是否正确（不改变任何状态，失败计入防爆破锁定）。

        供关闭登录二次验证后自检密钥可用性（换设备 / 手机时间漂移场景），
        避免重新开启时才发现码不对而连续失败触发锁定。
        """
        user = request.user
        if not user.otp_secret_key:
            return ApiResponse(code=1001, detail=_("OTP is not bound"))

        serializer = OtpBindConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        block = MFABlockUtils(user.username, get_request_ip(request))
        if block.is_block():
            return ApiResponse(code=1001, detail=_("Too many failures, the account has been locked"))
        if not OtpBackend.verify_code(user.otp_secret_key, serializer.validated_data["code"]):
            block.incr_failed_count()
            return ApiResponse(code=1002, detail=_("The OTP verification code is incorrect"))
        block.clean_failed_count()
        return ApiResponse(detail=_("Verification successful"))

    @extend_schema(responses=get_default_response_schema())
    @action(
        methods=["post"],
        detail=False,
        url_path="disable",
        permission_classes=[IsAuthenticated, UserConfirmation.require(ConfirmType.PASSWORD)],
    )
    def disable(self, request, *args, **kwargs):
        """解绑 OTP（敏感操作：需先通过二次验证，未验证时返回 412）

        恢复码随解绑一并作废——它是当前 OTP 密钥的配套自救凭据，密钥不在即无意义。
        """
        user = request.user
        if not user.otp_secret_key:
            return ApiResponse(code=1001, detail=_("OTP is not bound"))
        user.otp_secret_key = ""
        user.mfa_level = get_user_model().MFALevelChoices.DISABLED
        user.save(update_fields=["otp_secret_key", "mfa_level"])
        recovery.clear_codes(user)
        UserConfirmStateCache(user).clear()
        return ApiResponse(detail=_("OTP unbinding successful"))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="recovery-codes")
    def recovery_codes(self, request, *args, **kwargs):
        """查询剩余恢复码数量（未用数；不回显任何码面）"""
        return ApiResponse(data={"remaining": recovery.remaining_count(request.user)})

    @extend_schema(responses=get_default_response_schema())
    @action(
        methods=["post"],
        detail=False,
        url_path="recovery-codes/regenerate",
        permission_classes=[IsAuthenticated, UserConfirmation.require(ConfirmType.PASSWORD)],
    )
    def regenerate_recovery_codes(self, request, *args, **kwargs):
        """重新生成恢复码（敏感操作：需先通过密码二次验证，未验证时返回 412）

        旧码整批作废，新码明文仅本次响应内出现一次。
        """
        user = request.user
        if not user.otp_secret_key:
            return ApiResponse(code=1001, detail=_("OTP is not bound"))
        codes = recovery.generate_codes(user)
        return ApiResponse(data={"recovery_codes": codes}, detail=_("Recovery codes have been regenerated"))
