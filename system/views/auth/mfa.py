#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : mfa
"""登录 MFA 二次验证接口（匿名，凭 mfa_token 识别待验证用户）。

流程：密码登录接口返回 mfa_required + mfa_token + methods 后，
1. POST /api/system/login/mfa/send-code  挑战型方式（短信/邮件）下发验证码；
2. POST /api/system/login/mfa/verify     提交验证码，通过后签发正式 JWT。
"""
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from common.core.response import ApiResponse
from common.core.throttle import LoginThrottle
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from common.utils.verify_code import TokenTempCache
from mfa.services import check_user_mfa_code, send_user_mfa_code, validate_login_mfa_token
from system.utils.auth import ValidateError, get_token_lifetime
from system.views.auth.login import login_success

logger = get_logger(__name__)

# 登录 MFA 允许的验证方式（密码方式在登录场景无意义）
LOGIN_MFA_METHODS = ['otp', 'sms', 'email']
CHALLENGE_METHODS = ['sms', 'email']


def _get_mfa_user(request):
    """校验 mfa_token 并返回待验证用户，无效则直接抛业务异常"""
    user = validate_login_mfa_token(request.data.get('mfa_token'))
    if not user:
        raise ValidateError(_('Login verification expired, please log in again'))
    if not user.mfa_enabled:
        raise ValidateError(_('Operation failed. Abnormal data'))
    return user


class LoginMFASendCodeAPIView(APIView):
    """发送登录 MFA 挑战验证码"""
    permission_classes = []
    authentication_classes = []
    throttle_classes = [LoginThrottle]

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    'mfa_token': build_basic_type(OpenApiTypes.STR),
                    'method': build_basic_type(OpenApiTypes.STR),
                },
                required=['mfa_token', 'method'],
            )
        ),
        responses=get_default_response_schema()
    )
    def post(self, request, *args, **kwargs):
        """发送登录 MFA 验证码"""
        user = _get_mfa_user(request)
        method = request.data.get('method')
        if method not in CHALLENGE_METHODS:
            raise ValidateError(_('The verification method is unavailable'))
        ok, msg = send_user_mfa_code(user, method, request)
        if not ok:
            raise ValidateError(msg)
        return ApiResponse(detail=_('The verification code has been sent'))


class LoginMFAVerifyAPIView(APIView):
    """登录 MFA 二次验证，通过后签发正式 JWT"""
    permission_classes = []
    authentication_classes = []
    throttle_classes = [LoginThrottle]

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    'mfa_token': build_basic_type(OpenApiTypes.STR),
                    'method': build_basic_type(OpenApiTypes.STR),
                    'code': build_basic_type(OpenApiTypes.STR),
                },
                required=['mfa_token', 'method', 'code'],
            )
        ),
        responses=get_default_response_schema(
            {
                'data': build_object_type(
                    properties={
                        'refresh': build_basic_type(OpenApiTypes.STR),
                        'access': build_basic_type(OpenApiTypes.STR),
                        'access_token_lifetime': build_basic_type(OpenApiTypes.NUMBER),
                        'refresh_token_lifetime': build_basic_type(OpenApiTypes.NUMBER)
                    }
                )
            }
        )
    )
    def post(self, request, *args, **kwargs):
        """提交登录 MFA 验证码"""
        user = _get_mfa_user(request)
        method = request.data.get('method')
        code = request.data.get('code')
        if method not in LOGIN_MFA_METHODS:
            raise ValidateError(_('The verification method is unavailable'))

        ok, msg = check_user_mfa_code(user, method, code, request)
        if not ok:
            raise ValidateError(msg)

        TokenTempCache.expired_cache_token(request.data.get('mfa_token'))
        refresh = RefreshToken.for_user(user)
        result = {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }
        result.update(get_token_lifetime(user))
        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])
        login_success(request, user)
        return ApiResponse(data=result)
