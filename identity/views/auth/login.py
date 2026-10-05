#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : login
# author : ly_13
# date : 8/8/2024

from django.conf import settings
from django.contrib.auth import authenticate
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from common.base.utils import AESCipherV2
from common.core.response import ApiResponse
from common.core.throttle import LoginThrottle
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from common.utils.request import get_request_ip
from identity.models import UserInfo
from identity.services.auth_login import (
    complete_login,
    evaluate_login_policy_for_request,
    login_failed,
    login_success,
)
from identity.utils.auth import (
    check_is_block,
    check_token_and_captcha,
    get_token_lifetime,
    get_username_password,
    save_login_log,
    verify_sms_email_code,
)
from identity.utils.session import bind_session_claim, register_user_session
from settings.services import LoginBlockUtil
from system.services import UserLoginLog

logger = get_logger(__name__)


def _register_session_safe(request, user, login_type):
    """登记会话（失败仅告警不影响登录）。返回 UserSession 或 None。"""
    try:
        return register_user_session(request, user, login_type)
    except Exception:  # noqa: BLE001 会话管理属附加能力
        logger.warning("register user session failed", exc_info=True)
        return None


def _login_type_for(user) -> "UserLoginLog.LoginTypeChoices":
    """账密登录来源：LdapBindBackend 认证成功记 LDAP，其余按本地账密。"""
    if getattr(user, "_ldap_authenticated", False):
        return UserLoginLog.LoginTypeChoices.LDAP  # type: ignore[return-value]  # Choices 元类
    return UserLoginLog.LoginTypeChoices.USERNAME  # type: ignore[return-value]  # 同上


class SessionTokenObtainPairSerializer(TokenObtainPairSerializer):
    """账密登录用：签发后登记会话，并把 sid claim 写入 token（refresh/access 同源继承）。

    super().validate 返回的是已编码 token 串，按原串重新解码补 claim 再编码
    （jti/exp 均保留，OutstandingToken 按 jti 关联不受影响）。
    """

    def validate(self, attrs):
        data = super().validate(attrs)
        # LDAP bind 认证的登录（LdapBindBackend 成功）在登录日志中标记独立来源；
        # 本地/验证码路径不受影响
        login_type = _login_type_for(self.user)
        session = _register_session_safe(self.context.get("request"), self.user, login_type)
        if session:
            try:
                # simplejwt 标注入参为 Token，运行期接受已编码串
                refresh = RefreshToken(data["refresh"])
                data["refresh"], data["access"] = bind_session_claim(refresh, session.pk)
            except Exception:  # noqa: BLE001 claim 注入失败退回无 sid 行为
                logger.warning("bind session claim failed", exc_info=True)
        return data


class BasicLoginAPIView(TokenObtainPairView):
    """用户登录"""

    throttle_classes = [LoginThrottle]
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
    def post(self, request, *args, **kwargs):
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
            # 记失败日志前绑定用户（登录日志 creator 归属被策略拒绝的账号）
            request.user = user
            save_login_log(request, status=False)
            return ApiResponse(code=1001, detail=reject_detail)
        mfa_response = complete_login(request, user, login_type=_login_type_for(user), force_mfa=force_mfa)
        if mfa_response:
            return mfa_response
        data = serializer.validated_data
        data.update(get_token_lifetime(user))
        # 强制改密标记：前端登录后引导改密（改密成功自动清除）
        data["must_change_password"] = bool(getattr(user, "must_change_password", False))
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
    def get(self, request, *args, **kwargs):
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


class VerifyCodeLoginAPIView(TokenObtainPairView):
    """用户验证码登录"""

    throttle_classes = [LoginThrottle]

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
    def post(self, request, *args, **kwargs):
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
                login_failed(request, target)
            # 登录访问策略（验证码 + 密码组合登录同样收口）
            force_mfa, reject_detail = evaluate_login_policy_for_request(request, user, ipaddr)
            if reject_detail:
                request.user = user
                save_login_log(request, status=False)
                return ApiResponse(code=1001, detail=reject_detail)
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
                request.user = user
                save_login_log(request, status=False, login_type=UserLoginLog.get_login_type(query_key))
                return ApiResponse(code=1001, detail=reject_detail)

        login_type = UserLoginLog.get_login_type(query_key)
        session = _register_session_safe(request, user, login_type)
        refresh = RefreshToken.for_user(user)
        if session:
            try:
                refresh_str, access_str = bind_session_claim(refresh, session.pk)
                result = {"refresh": refresh_str, "access": access_str}
            except Exception:  # noqa: BLE001 claim 注入失败退回无 sid 行为
                logger.warning("bind session claim failed", exc_info=True)
                result = {"refresh": str(refresh), "access": str(refresh.access_token)}
        else:
            result = {"refresh": str(refresh), "access": str(refresh.access_token)}
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        result.update(**get_token_lifetime(user))
        result["must_change_password"] = bool(getattr(user, "must_change_password", False))
        login_success(request, user, login_type=login_type)
        return ApiResponse(data=result)
