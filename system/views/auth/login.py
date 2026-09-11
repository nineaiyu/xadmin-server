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
from drf_spectacular.plumbing import build_object_type, build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from common.base.utils import AESCipherV2
from common.core.response import ApiResponse
from common.core.throttle import LoginThrottle
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from common.utils.ip import get_ip_city
from common.utils.request import get_request_ip, get_browser, get_os
from mfa.services import generate_login_mfa_token, get_login_mfa_methods, is_login_mfa_required
from settings.services import (
    PASSWORD_EXPIRED_MESSAGE,
    LoginBlockUtil,
    LoginIpBlockUtil,
    is_password_expired,
)
from system.models import UserInfo, UserLoginLog
from system.utils.auth import (
    get_username_password,
    get_token_lifetime,
    check_is_block,
    check_token_and_captcha,
    save_login_log,
    verify_sms_email_code,
    check_different_city_login_if_need,
    ValidateError,
)

from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from system.utils.session import bind_session_claim, register_user_session

logger = get_logger(__name__)


def _register_session_safe(request, user, login_type):
    """登记会话（失败仅告警不影响登录）。返回 UserSession 或 None。"""
    try:
        return register_user_session(request, user, login_type)
    except Exception:  # noqa: BLE001 会话管理属附加能力
        logger.warning("register user session failed", exc_info=True)
        return None


class SessionTokenObtainPairSerializer(TokenObtainPairSerializer):
    """账密登录用：签发后登记会话，并把 sid claim 写入 token（refresh/access 同源继承）。

    super().validate 返回的是已编码 token 串，按原串重新解码补 claim 再编码
    （jti/exp 均保留，OutstandingToken 按 jti 关联不受影响）。
    """

    def validate(self, attrs):
        data = super().validate(attrs)
        session = _register_session_safe(self.context.get("request"), self.user, UserLoginLog.LoginTypeChoices.USERNAME)
        if session:
            try:
                refresh = RefreshToken(data["refresh"])
                data["refresh"], data["access"] = bind_session_claim(refresh, session.pk)
            except Exception:  # noqa: BLE001 claim 注入失败退回无 sid 行为
                logger.warning("bind session claim failed", exc_info=True)
        return data


def login_failed(request, username):
    ipaddr = get_request_ip(request)
    login_block_util = LoginBlockUtil(username, ipaddr)
    login_ip_block = LoginIpBlockUtil(ipaddr)
    request.user = UserInfo.objects.filter(username=username).first()
    save_login_log(request, status=False)
    login_block_util.incr_failed_count()
    login_ip_block.set_block_if_need()

    times_remainder = login_block_util.get_remainder_times()
    if times_remainder > 0:
        detail = _(
            "The username or password you entered is incorrect, "
            "please enter it again. "
            "You can also try {times_try} times "
            "(The account will be temporarily locked for {block_time} minutes)"
        ).format(times_try=times_remainder, block_time=settings.SECURITY_LOGIN_LIMIT_TIME)
    else:
        detail = _(
            "The account has been locked (please contact admin to unlock it or try again after {} minutes)"
        ).format(settings.SECURITY_LOGIN_LIMIT_TIME)
    raise ValidateError(detail)


def login_success(request, user_obj, login_type=UserLoginLog.LoginTypeChoices.USERNAME, save_log=True):
    if is_password_expired(user_obj):
        # 密码有效期拦截（SECURITY_PASSWORD_EXPIRATION_DAYS，默认关闭）：MFA 前收口，
        # 待二次验证路径同样拦截；date_password_updated 为空的存量用户宽限放行
        raise ValidateError(PASSWORD_EXPIRED_MESSAGE)
    ipaddr = get_request_ip(request)
    login_block_util = LoginBlockUtil(user_obj.username, ipaddr)
    login_ip_block = LoginIpBlockUtil(ipaddr)
    login_block_util.clean_failed_count()
    login_ip_block.clean_block_if_need()
    if not save_log:
        # 登录 MFA 待验证：密码阶段已通过，锁定计数需清理；登录日志与异地提醒在二次验证通过后记录
        return
    request.user = user_obj
    check_different_city_login_if_need(user_obj, ipaddr)
    if login_type != UserLoginLog.LoginTypeChoices.WEBSOCKET:
        # 新设备/新 IP/新城市登录提醒（默认关闭；内部全吞异常，绝不影响登录）。
        # WS 接入不触发：页面伴随登录已提醒过，WS 再提醒只制造重复噪音（计划登记边界）
        from system.utils.login_alert import maybe_alert_abnormal_login

        maybe_alert_abnormal_login(
            user_obj,
            ipaddr,
            str(get_ip_city(ipaddr) or ""),
            get_browser(request),
            get_os(request),
        )
    save_login_log(request, login_type=login_type)


def login_mfa_if_required(request, user_obj):
    """用户开启登录 MFA 时返回 True：清理密码阶段锁定计数（登录日志在二次验证通过后记录）"""
    if not is_login_mfa_required(user_obj):
        return False
    if not get_login_mfa_methods(user_obj, request):
        # 已开启 MFA 但无可用验证方式（如管理员关闭了全部方式），降级放行避免登录死锁
        logger.warning("Login MFA required but no available method, skip. user: %s", user_obj.username)
        return False
    login_success(request, user_obj, save_log=False)
    return True


def complete_login(request, user_obj, login_type=UserLoginLog.LoginTypeChoices.USERNAME):
    """登录成功后的**唯一收口**：MFA 判定 → 会话登记 / 登录日志 / 异常提醒 / 锁定计数清理。

    任何新增登录路径（本地密码 / 验证码 / WebSocket / 第三方 OAuth…）都必须调用它：
    绕过这里等于同时绕过登录 MFA、``UserSession`` 登记、登录日志与失败计数清理
    （历史教训：MFA 只在本地密码链路生效时，新增登录方式就是一条后门）。

    :return: 需要 MFA 二次验证时返回可直接下发的 ``ApiResponse``；否则返回 ``None``，
             由调用方继续下发自己的 token 载荷。
    """
    if login_mfa_if_required(request, user_obj):
        return ApiResponse(
            data={
                "mfa_required": True,
                "mfa_token": generate_login_mfa_token(user_obj),
                "methods": get_login_mfa_methods(user_obj, request),
            }
        )
    login_success(request, user_obj, login_type=login_type)
    return None


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
            return login_failed(request, username)
        user = serializer.user
        mfa_response = complete_login(request, user)
        if mfa_response:
            return mfa_response
        data = serializer.validated_data
        data.update(get_token_lifetime(user))
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
            mfa_response = complete_login(request, user, login_type=UserLoginLog.LoginTypeChoices.USERNAME)
            if mfa_response:
                return mfa_response
        else:
            # 验证码登录本身已通过动态因子（短信/邮件验证码）验证，无需再走 MFA
            user = UserInfo.objects.get(**{query_key: target})

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
        login_success(request, user, login_type=login_type)
        return ApiResponse(data=result)
