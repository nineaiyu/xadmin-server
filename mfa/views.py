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
from mfa.backends.otp import OtpBackend
from mfa.cache import OtpBindCache, UserConfirmStateCache
from mfa.confirm import UserConfirmation
from mfa.const import CONFIRM_TYPE_TTL_SETTING, ConfirmType
from mfa.serializers import ConfirmSerializer, OtpBindConfirmSerializer, SendCodeSerializer
from mfa.services import get_confirm_methods, send_user_mfa_code, verify_user_confirm
from settings.services import MFABlockUtils

logger = get_logger(__name__)


def _get_confirm_type(value):
    return value if value in ConfirmType.values else ConfirmType.MFA


def _state_expire_at(state):
    if not state:
        return None
    ttl = int(getattr(settings, CONFIRM_TYPE_TTL_SETTING[state['type']]))
    return int(state['time'] + ttl)


class UserConfirmViewSet(GenericViewSet):
    """敏感操作二次验证

    前端交互流程：请求敏感 API 收到 412（type=user_confirm_required）后，
    1. GET  /api/mfa/confirm?confirm_type=mfa      获取可用验证方式渲染验证弹窗；
    2. POST /api/mfa/confirm/send-code             挑战型方式（短信/邮件）先下发验证码；
    3. POST /api/mfa/confirm                       提交验证，通过后有效期内免重复验证。
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[{'name': 'confirm_type', 'in': 'query', 'schema': {'type': 'string', 'enum': ConfirmType.values}}],
        responses=get_default_response_schema()
    )
    def retrieve(self, request, *args, **kwargs):
        """获取可用验证方式与当前确认状态"""
        confirm_type = _get_confirm_type(request.query_params.get('confirm_type'))
        state_cache = UserConfirmStateCache(request.user)
        state = state_cache.get()
        return ApiResponse(data={
            'confirm_type': confirm_type,
            'methods': get_confirm_methods(request.user, request, confirm_type),
            'confirmed': state_cache.is_valid_for(confirm_type),
            'expire_at': _state_expire_at(state),
        })

    @extend_schema(request=ConfirmSerializer, responses=get_default_response_schema())
    def create(self, request, *args, **kwargs):
        """提交验证：校验通过后写入确认状态"""
        serializer = ConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        confirm_type = serializer.validated_data['confirm_type']
        ok, msg = verify_user_confirm(
            request.user, serializer.validated_data['method'], serializer.validated_data['code'],
            request=request, confirm_type=confirm_type
        )
        if not ok:
            return ApiResponse(code=1002, detail=msg)
        return ApiResponse(data={'expire_at': _state_expire_at(UserConfirmStateCache(request.user).get())},
                           detail=_('Verification successful'))

    @extend_schema(request=SendCodeSerializer, responses=get_default_response_schema())
    @action(methods=['post'], detail=False, url_path='send-code', serializer_class=SendCodeSerializer)
    def send_code(self, request, *args, **kwargs):
        """发送挑战验证码（短信/邮件）"""
        serializer = SendCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ok, msg = send_user_mfa_code(request.user, serializer.validated_data['method'], request=request)
        if not ok:
            return ApiResponse(code=1002, detail=msg)
        return ApiResponse(detail=_('The verification code has been sent'))


class UserOTPViewSet(GenericViewSet):
    """个人 OTP(TOTP) 绑定管理"""
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=get_default_response_schema())
    def retrieve(self, request, *args, **kwargs):
        """获取绑定状态"""
        return ApiResponse(data={
            'enabled': request.user.mfa_enabled,
            'phone': request.user.phone,
            'email': request.user.email,
        })

    @extend_schema(responses=get_default_response_schema())
    @action(methods=['post'], detail=False, url_path='start')
    def start(self, request, *args, **kwargs):
        """发起绑定：生成候选密钥与 otpauth URI（前端渲染二维码）"""
        if request.user.mfa_enabled:
            return ApiResponse(code=1001, detail=_('OTP is already bound'))
        bind_cache = OtpBindCache(request.user)
        secret = bind_cache.get_secret()
        if not secret:
            secret = OtpBackend.generate_secret()
            bind_cache.set_secret(secret)
        return ApiResponse(data={'secret': secret, 'uri': OtpBackend.get_provisioning_uri(request.user, secret)})

    @extend_schema(request=OtpBindConfirmSerializer, responses=get_default_response_schema())
    @action(methods=['post'], detail=False, url_path='confirm', serializer_class=OtpBindConfirmSerializer)
    def confirm(self, request, *args, **kwargs):
        """确认绑定：校验动态码后写入密钥，并自动开启登录 MFA"""
        user = request.user
        if user.mfa_enabled:
            return ApiResponse(code=1001, detail=_('OTP is already bound'))
        secret = OtpBindCache(user).get_secret()
        if not secret:
            return ApiResponse(code=1001, detail=_('Please start binding first'))

        serializer = OtpBindConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        block = MFABlockUtils(user.username, get_request_ip(request))
        if block.is_block():
            return ApiResponse(code=1001, detail=_('Too many failures, the account has been locked'))
        if not OtpBackend.verify_code(secret, serializer.validated_data['code']):
            block.incr_failed_count()
            return ApiResponse(code=1002, detail=_('The OTP verification code is incorrect'))
        block.clean_failed_count()

        user.otp_secret_key = secret
        user.mfa_level = get_user_model().MFALevelChoices.ENABLED
        user.save(update_fields=['otp_secret_key', 'mfa_level'])
        OtpBindCache(user).clear()
        return ApiResponse(detail=_('OTP binding successful'))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=['post'], detail=False, url_path='disable',
            permission_classes=[IsAuthenticated, UserConfirmation.require(ConfirmType.PASSWORD)])
    def disable(self, request, *args, **kwargs):
        """解绑 OTP（敏感操作：需先通过二次验证，未验证时返回 412）"""
        user = request.user
        user.otp_secret_key = ''
        user.mfa_level = get_user_model().MFALevelChoices.DISABLED
        user.save(update_fields=['otp_secret_key', 'mfa_level'])
        UserConfirmStateCache(user).clear()
        return ApiResponse(detail=_('OTP unbinding successful'))
