#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : mfa
"""登录 MFA 二次验证接口（匿名，凭 mfa_token 识别待验证用户）。

流程：密码登录接口返回 mfa_required + mfa_token + methods 后，
1. POST /api/identity/login/mfa/send-code  挑战型方式（短信/邮件）下发验证码；
2. POST /api/identity/login/mfa/verify     提交验证码，通过后签发正式 JWT。
"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.views import APIView

from audit.services import UserLoginLog
from common.core.response import ApiResponse
from common.core.throttle import LoginThrottle
from common.swagger.utils import get_default_response_schema
from common.utils.request import get_request_ip
from common.utils.verify_code import TokenTempCache
from identity.services.auth_login import (
    complete_login,
    evaluate_login_policy_for_request,
    reject_login,
)
from identity.services.token_issue_login import issue_session_token
from identity.utils.auth import ValidateError
from mfa.services import (
    check_user_mfa_code,
    get_login_mfa_methods,
    send_user_mfa_code,
    validate_login_mfa_token,
)

# 登录 MFA 允许的验证方式（密码方式在登录场景无意义；recovery 为 OTP 恢复码自救通道）
LOGIN_MFA_METHODS = ["otp", "sms", "email", "passkey", "recovery"]
CHALLENGE_METHODS = ["sms", "email"]


def _get_mfa_user(request: Any) -> Any:
    """校验 mfa_token 并返回待验证用户，无效则直接抛业务异常。

    只要求登录 MFA 至少有一种当前可用方式（OTP / Passkey / 短信 / 邮件 / 恢复码）：
    角色或登录策略强制 MFA 时，仅配置短信/邮箱的账号也曾能拿到 mfa_token，
    却被这里按「必须有 OTP/Passkey」拒绝，密码正确也永远登不进。
    具体方式是否可用仍由 ``check_user_mfa_code`` / ``send_user_mfa_code`` 按策略校验。
    """
    user = validate_login_mfa_token(request.data.get("mfa_token"))
    if not user:
        raise ValidateError(_("Login verification expired, please log in again"))
    if not get_login_mfa_methods(user, request):
        raise ValidateError(_("No available login verification method"))
    return user


class LoginMFAPasskeyChallengeAPIView(APIView):
    """Passkey 登录验证挑战

    匿名接口：凭一次性 ``mfa_token`` 识别待验证用户。
    """

    permission_classes: list[type] = []
    authentication_classes: list[type] = []
    throttle_classes = [LoginThrottle]

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"mfa_token": build_basic_type(OpenApiTypes.STR)},
                required=["mfa_token"],
            )
        ),
        responses=get_default_response_schema(),
    )
    def post(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取 Passkey 挑战值"""
        from identity.utils.webauthn import SCENE_AUTHENTICATE, generate_challenge, rp_id_and_origin

        user = _get_mfa_user(request)
        rp_id, _origin = rp_id_and_origin(request)
        return ApiResponse(data={"challenge": generate_challenge(user, SCENE_AUTHENTICATE), "rp_id": rp_id})


class LoginMFASendCodeAPIView(APIView):
    """发送登录 MFA 挑战验证码"""

    permission_classes: list[type] = []
    authentication_classes: list[type] = []
    throttle_classes = [LoginThrottle]

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "mfa_token": build_basic_type(OpenApiTypes.STR),
                    "method": build_basic_type(OpenApiTypes.STR),
                },
                required=["mfa_token", "method"],
            )
        ),
        responses=get_default_response_schema(),
    )
    def post(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """发送登录 MFA 验证码"""
        user = _get_mfa_user(request)
        method = request.data.get("method")
        if method not in CHALLENGE_METHODS:
            raise ValidateError(_("The verification method is unavailable"))
        ok, msg = send_user_mfa_code(user, method, request)
        if not ok:
            raise ValidateError(msg)
        return ApiResponse(detail=_("The verification code has been sent"))


class LoginMFAVerifyAPIView(APIView):
    """登录 MFA 二次验证，通过后签发正式 JWT"""

    permission_classes: list[type] = []
    authentication_classes: list[type] = []
    throttle_classes = [LoginThrottle]

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "mfa_token": build_basic_type(OpenApiTypes.STR),
                    "method": build_basic_type(OpenApiTypes.STR),
                    "code": build_basic_type(OpenApiTypes.STR),
                },
                required=["mfa_token", "method", "code"],
            )
        ),
        responses=get_default_response_schema(
            {
                "data": build_object_type(
                    properties={
                        "refresh": build_basic_type(OpenApiTypes.STR),
                        "access": build_basic_type(OpenApiTypes.STR),
                        "access_token_lifetime": build_basic_type(OpenApiTypes.NUMBER),
                        "refresh_token_lifetime": build_basic_type(OpenApiTypes.NUMBER),
                    }
                )
            }
        ),
    )
    def post(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """提交登录 MFA 验证码"""
        user = _get_mfa_user(request)
        method = request.data.get("method")
        code = request.data.get("code")
        if method not in LOGIN_MFA_METHODS:
            raise ValidateError(_("The verification method is unavailable"))

        ok, msg = check_user_mfa_code(user, method, code, request)
        if not ok:
            raise ValidateError(msg)

        # 二次验证通过后再复核全局登录开关与访问策略：密码阶段到 MFA 提交之间
        # 管理员可能关闭登录或收紧策略；策略的「强制 MFA」已由本次验证满足，
        # 因此只处理拒绝分支，不再触发第二次 MFA 挑战。
        _force_mfa, reject_detail = evaluate_login_policy_for_request(request, user, get_request_ip(request))
        if reject_detail:
            return reject_login(
                request,
                user,
                reject_detail,
                login_type=UserLoginLog.LoginTypeChoices.USERNAME,
            )
        mfa_response = complete_login(
            request,
            user,
            login_type=UserLoginLog.LoginTypeChoices.USERNAME,
            policy_checked=True,
            ignore_user_mfa=True,
        )
        if mfa_response:
            return mfa_response
        TokenTempCache.expired_cache_token(request.data.get("mfa_token"))
        # 会话登记 + sid claim 绑定 + 生命周期 + 改密标记 + last_login（与账密/验证码登录同口径）
        result = issue_session_token(request, user, UserLoginLog.LoginTypeChoices.USERNAME)
        return ApiResponse(data=result)
