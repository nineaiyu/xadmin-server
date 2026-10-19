#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : login
# author : ly_13
# date : 8/8/2024
from typing import Any

from django.conf import settings
from django.contrib.auth import authenticate
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

from audit.services import UserLoginLog
from common.base.utils import AESCipherV2
from common.core.response import ApiResponse
from common.core.throttle import LoginThrottle
from common.swagger.utils import get_default_response_schema
from common.utils.request import get_request_ip
from identity.models import UserInfo
from identity.services.auth_login import (
    complete_login as complete_login,  # noqa: F401 显式再导出（PEP 484 语义）
)
from identity.services.auth_login import (
    evaluate_login_policy_for_request,
    login_failed,
    login_success,
    reject_login,
)
from identity.services.token_issue_login import issue_session_token
from identity.utils.auth import (
    check_is_block,
    check_token_and_captcha,
    get_username_password,
    verify_sms_email_code,
)
from settings.services import LoginBlockUtil


def _login_type_for(user: Any) -> "UserLoginLog.LoginTypeChoices":
    """账密登录来源：LdapBindBackend 认证成功记 LDAP，其余按本地账密。"""
    if getattr(user, "_ldap_authenticated", False):
        return UserLoginLog.LoginTypeChoices.LDAP
    return UserLoginLog.LoginTypeChoices.USERNAME


class SessionTokenObtainPairSerializer(TokenObtainPairSerializer):
    """账密登录用序列化器：只保留类型标识，校验外无副作用。

    会话登记与 token 签发（含 sid claim、生命周期、改密标记、last_login）已收敛到
    :func:`identity.services.token_issue_login.issue_session_token`，在
    ``BasicLoginAPIView.post()`` 的 ``complete_login`` 之后调用——serializer 的
    ``validate`` 不再创建数据库行或改写 token 编码。
    """


class BasicLoginAPIView(TokenObtainPairView):
    """用户登录"""

    throttle_classes = [LoginThrottle]
    # 纯校验子类：会话登记与 token 签发已移到 post()（经 issue_session_token），
    # serializer 不再承担数据库副作用
    serializer_class = SessionTokenObtainPairSerializer

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "username": build_basic_type(OpenApiTypes.STR),
                    "password": build_basic_type(OpenApiTypes.STR),
                    "token": build_basic_type(OpenApiTypes.STR),
                    "captcha_key": build_basic_type(OpenApiTypes.STR),
                    "captcha_code": build_basic_type(OpenApiTypes.STR),
                },
                required=["username", "password"],
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
        """用户名密码登录"""
        if not settings.SECURITY_LOGIN_ACCESS_ENABLED:
            return ApiResponse(code=1001, detail=_("Login forbidden"))

        ipaddr = get_request_ip(request)
        client_id, token = check_token_and_captcha(
            request, settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED, settings.SECURITY_LOGIN_CAPTCHA_ENABLED
        )

        username, password = get_username_password(settings.SECURITY_LOGIN_ENCRYPTED_ENABLED, request, token)

        check_is_block(username, ipaddr)

        serializer = self.get_serializer(data={"username": username, "password": password})
        try:
            serializer.is_valid(raise_exception=True)
        except Exception:
            # 校验失败（含凭证错误）：统一按登录失败计数并返回通用文案（不回显差异，防账号枚举）
            return login_failed(request, username)
        user = serializer.user
        # 登录访问策略：密码校验通过后判定（避免匿名探测策略信息），命中写入登录日志
        force_mfa, reject_detail = evaluate_login_policy_for_request(request, user, ipaddr)
        if reject_detail:
            return reject_login(request, user, reject_detail, login_type=_login_type_for(user))
        mfa_response = complete_login(request, user, login_type=_login_type_for(user), force_mfa=force_mfa)
        if mfa_response:
            return mfa_response
        data = issue_session_token(request, user, _login_type_for(user))
        return ApiResponse(data=data)

    @extend_schema(
        responses=get_default_response_schema(
            {
                "data": build_object_type(
                    properties={
                        "access": build_basic_type(OpenApiTypes.BOOL),
                        "captcha": build_basic_type(OpenApiTypes.BOOL),
                        "token": build_basic_type(OpenApiTypes.BOOL),
                        "encrypted": build_basic_type(OpenApiTypes.BOOL),
                        "lifetime": build_basic_type(OpenApiTypes.NUMBER),
                        "reset": build_basic_type(OpenApiTypes.BOOL),
                        "basic": build_basic_type(OpenApiTypes.BOOL),
                    }
                )
            }
        )
    )
    def get(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取登录配置信息"""
        config = {
            "access": settings.SECURITY_LOGIN_ACCESS_ENABLED,
            "captcha": settings.SECURITY_LOGIN_CAPTCHA_ENABLED,
            "token": settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED,
            "encrypted": settings.SECURITY_LOGIN_ENCRYPTED_ENABLED,
            "lifetime": settings.SIMPLE_JWT.get("REFRESH_TOKEN_LIFETIME").days,
            "reset": settings.SECURITY_RESET_PASSWORD_ACCESS_ENABLED,
            "basic": settings.SECURITY_LOGIN_BY_BASIC_ENABLED,
        }
        return ApiResponse(data=config)


class VerifyCodeLoginAPIView(GenericAPIView):
    """用户验证码登录

    GenericAPIView：本视图不使用 simplejwt 序列化器（authenticate + 手签 token），
    继承 TokenObtainPairView 只会额外暴露其 GET 配置行为。登录前置须匿名可达，
    故显式清空认证与权限链（与 TokenObtainPairView 家族同口径）。
    """

    throttle_classes = [LoginThrottle]
    permission_classes: list[type] = []
    authentication_classes: list[type] = []

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "password": build_basic_type(OpenApiTypes.STR),
                    "verify_token": build_basic_type(OpenApiTypes.STR),
                    "verify_code": build_basic_type(OpenApiTypes.STR),
                },
                required=["verify_token", "verify_code"],
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
        """验证码登录"""
        if not settings.SECURITY_LOGIN_ACCESS_ENABLED:
            return ApiResponse(code=1001, detail=_("Login forbidden"))
        ipaddr = get_request_ip(request)
        query_key, target, verify_token = verify_sms_email_code(request, LoginBlockUtil)
        check_is_block(target, ipaddr)

        if query_key == "username":
            password = request.data.get("password")
            if settings.SECURITY_LOGIN_ENCRYPTED_ENABLED:
                password = AESCipherV2(verify_token).decrypt(password)
            user = authenticate(**{query_key: target}, password=password)
            if not user:
                # login_failed 内部抛异常，return 仅为显式终止（语义不依赖栈展开）
                return login_failed(request, target)
            # 登录访问策略（验证码 + 密码组合登录同样收口）
            force_mfa, reject_detail = evaluate_login_policy_for_request(request, user, ipaddr)
            if reject_detail:
                return reject_login(request, user, reject_detail, login_type=UserLoginLog.get_login_type(query_key))
            mfa_response = complete_login(
                request, user, login_type=UserLoginLog.LoginTypeChoices.USERNAME, force_mfa=force_mfa
            )
            if mfa_response:
                return mfa_response
        else:
            # 验证码登录本身已通过动态因子（短信/邮件验证码）验证，无需再走 MFA
            user = UserInfo.objects.get(**{query_key: target})
            force_mfa, reject_detail = evaluate_login_policy_for_request(request, user, ipaddr)
            if reject_detail:
                return reject_login(request, user, reject_detail, login_type=UserLoginLog.get_login_type(query_key))

        login_type = UserLoginLog.get_login_type(query_key)
        result = issue_session_token(request, user, login_type)
        login_success(request, user, login_type=login_type)
        return ApiResponse(data=result)
