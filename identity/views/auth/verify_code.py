#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : code
# author : ly_13
# date : 8/10/2024
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiRequest, extend_schema, extend_schema_view
from rest_framework.exceptions import APIException
from rest_framework.generics import GenericAPIView

from common.base.utils import AESCipherV2
from common.core.response import ApiResponse
from common.core.throttle import VerifyCodeThrottle
from common.fields.utils import get_file_absolute_uri
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger, random_string
from common.utils.request import get_request_ip
from common.utils.verify_code import SendAndVerifyCodeUtil, TokenTempCache
from identity.models import UserInfo
from identity.utils.auth import ValidateError, check_is_block, check_token_and_captcha
from settings.services import LoginIpBlockUtil, SendVerifyCodeBlockUtil, get_password_check_rules

logger = get_logger(__name__)


def get_register_config(request: Any) -> Any:
    config = {
        "access": settings.SECURITY_REGISTER_ACCESS_ENABLED,
        "captcha": settings.SECURITY_REGISTER_CAPTCHA_ENABLED,
        "token": settings.SECURITY_REGISTER_TEMP_TOKEN_ENABLED,
        "encrypted": settings.SECURITY_REGISTER_ENCRYPTED_ENABLED,
        "email": settings.SECURITY_REGISTER_BY_EMAIL_ENABLED and settings.EMAIL_ENABLED,
        "sms": settings.SECURITY_REGISTER_BY_SMS_ENABLED and settings.SMS_ENABLED,
        "rate": settings.VERIFY_CODE_LIMIT,
        "password": get_password_check_rules(request.user),
    }
    return config


def get_login_config(request: Any) -> Any:
    config = {
        "access": settings.SECURITY_LOGIN_ACCESS_ENABLED,
        "captcha": settings.SECURITY_LOGIN_CAPTCHA_ENABLED,
        "token": settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED,
        "encrypted": settings.SECURITY_LOGIN_ENCRYPTED_ENABLED,
        "email": settings.SECURITY_LOGIN_BY_EMAIL_ENABLED and settings.EMAIL_ENABLED,
        "sms": settings.SECURITY_LOGIN_BY_SMS_ENABLED and settings.SMS_ENABLED,
        "rate": settings.VERIFY_CODE_LIMIT,
        "lifetime": settings.SIMPLE_JWT.get("REFRESH_TOKEN_LIFETIME").days,
        "reset": settings.SECURITY_RESET_PASSWORD_ACCESS_ENABLED,
        "register": settings.SECURITY_REGISTER_ACCESS_ENABLED,
    }
    return config


def get_reset_config(request: Any) -> Any:
    config = {
        "access": settings.SECURITY_RESET_PASSWORD_ACCESS_ENABLED,
        "captcha": settings.SECURITY_RESET_PASSWORD_CAPTCHA_ENABLED,
        "token": settings.SECURITY_RESET_PASSWORD_TEMP_TOKEN_ENABLED,
        "encrypted": settings.SECURITY_RESET_PASSWORD_ENCRYPTED_ENABLED,
        "email": settings.SECURITY_RESET_PASSWORD_BY_EMAIL_ENABLED and settings.EMAIL_ENABLED,
        "sms": settings.SECURITY_RESET_PASSWORD_BY_SMS_ENABLED and settings.SMS_ENABLED,
        "rate": settings.VERIFY_CODE_LIMIT,
        "password": get_password_check_rules(request.user),
    }
    return config


def check_register_config(request: Any, form_type: Any, query_key: Any, target: Any) -> Any:
    """注册场景：目标已存在时明确告知（注册流程必须提示占用，否则用户无法完成注册）。

    返回 ``(username, extra, should_send)``；注册链路始终发送验证码。
    """
    extra = request.data.get("extra", {})
    if form_type == "sms":
        detail = _("Phone already exist")
    elif form_type == "email":
        detail = _("Email already exist")
    else:
        detail = _("Username already exist")

    user = UserInfo.objects.filter(**{query_key: target}).exists()
    if user:
        raise ValidateError(detail)
    return "", extra, True


def check_reset_config(request: Any, form_type: Any, query_key: Any, target: Any) -> Any:
    """重置/登录场景：不区分账号是否存在（防用户枚举）。

    账号不存在（或已停用）时不发送验证码，但同样返回成功与统一文案，
    后续验证码校验必然失败——攻击者无法据此判断账号是否存在；
    注册场景（check_register_config）保留明确文案。
    """
    extra = request.data.get("extra", {})
    user = UserInfo.objects.filter(is_active=True, **{query_key: target}).first()
    if not user:
        return "", extra, False
    return user.username, extra, True


def get_bind_email_config(request: Any) -> Any:
    config = {
        "access": settings.SECURITY_BIND_EMAIL_ACCESS_ENABLED,
        "captcha": settings.SECURITY_BIND_EMAIL_CAPTCHA_ENABLED,
        "token": settings.SECURITY_BIND_EMAIL_TEMP_TOKEN_ENABLED,
        "encrypted": settings.SECURITY_BIND_EMAIL_ENCRYPTED_ENABLED,
        "email": settings.EMAIL_ENABLED,
        "rate": settings.VERIFY_CODE_LIMIT,
    }
    return config


def check_bind_email_config(request: Any, form_type: Any, query_key: Any, target: Any) -> Any:
    """绑定场景：回显目标用户资料（绑定流程需要展示「绑定到哪个账号」）。"""
    extra = request.data.get("extra", {})
    user = UserInfo.objects.filter(**{query_key: target}).first()
    if user:
        extra["avatar"] = get_file_absolute_uri(user.avatar, request)
        extra["username"] = user.username
        extra["nickname"] = user.nickname
    return "", extra, True


def get_bind_phone_config(request: Any) -> Any:
    config = {
        "access": settings.SECURITY_BIND_PHONE_ACCESS_ENABLED,
        "captcha": settings.SECURITY_BIND_PHONE_CAPTCHA_ENABLED,
        "token": settings.SECURITY_BIND_PHONE_TEMP_TOKEN_ENABLED,
        "encrypted": settings.SECURITY_BIND_PHONE_ENCRYPTED_ENABLED,
        "sms": settings.SMS_ENABLED,
        "rate": settings.VERIFY_CODE_LIMIT,
    }
    return config


@dataclass
class VerifyCodeCategory:
    """验证码场景（category）的配置与校验入口。

    ``config`` 取场景开关配置；``check`` 返回 ``(username, extra, should_send)``。
    非 frozen：集成测试按场景定向替换 ``check``（monkeypatch 表项）后需可还原。
    """

    key: str
    #: (request) -> dict：场景开关配置
    config: Callable[[Any], dict[str, Any]]
    #: (request, form_type, query_key, target) -> (username, extra, should_send)
    check: Callable[..., tuple[str, dict[str, Any], bool]]


#: 场景表：新增场景只需在此登记一行（CATEGORY_KEYS 与 swagger enum 均从本表派生）。
#: 「登录与重置同口径（不区分账号存在性，防枚举）」在此显式表达，不再靠转发方法隐藏。
CATEGORIES: dict[str, VerifyCodeCategory] = {
    "register": VerifyCodeCategory("register", get_register_config, check_register_config),
    "login": VerifyCodeCategory("login", get_login_config, check_reset_config),
    "reset": VerifyCodeCategory("reset", get_reset_config, check_reset_config),
    "bind_email": VerifyCodeCategory("bind_email", get_bind_email_config, check_bind_email_config),
    "bind_phone": VerifyCodeCategory("bind_phone", get_bind_phone_config, check_bind_email_config),
}

#: 允许的场景键（单一事实源，与 CATEGORIES 键集一致）
CATEGORY_KEYS: tuple[str, ...] = tuple(CATEGORIES)


@extend_schema_view(
    get=extend_schema(
        description="获取发送验证码配置信息",
        parameters=[
            OpenApiParameter(
                name="category",
                required=True,
                type=OpenApiTypes.STR,
                enum=list(CATEGORY_KEYS),
            )
        ],
        responses=get_default_response_schema(
            {
                "data": build_object_type(
                    properties={
                        "access": build_basic_type(OpenApiTypes.BOOL),
                        "captcha": build_basic_type(OpenApiTypes.BOOL),
                        "token": build_basic_type(OpenApiTypes.BOOL),
                        "encrypted": build_basic_type(OpenApiTypes.BOOL),
                        "email": build_basic_type(OpenApiTypes.BOOL),
                        "sms": build_basic_type(OpenApiTypes.BOOL),
                        "rate": build_basic_type(OpenApiTypes.NUMBER),
                        "lifetime": build_basic_type(OpenApiTypes.NUMBER),
                        "reset": build_basic_type(OpenApiTypes.BOOL),
                        "register": build_basic_type(OpenApiTypes.BOOL),
                        "password": build_array_type(
                            build_object_type(
                                properties={
                                    "key": build_basic_type(OpenApiTypes.STR),
                                    "value": build_basic_type(OpenApiTypes.NUMBER),
                                }
                            )
                        ),
                    }
                )
            }
        ),
    ),
    post=extend_schema(
        description="发送验证码",
        parameters=[
            OpenApiParameter(
                name="category",
                required=True,
                type=OpenApiTypes.STR,
                enum=list(CATEGORY_KEYS),
            )
        ],
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "form_type": build_basic_type(OpenApiTypes.STR),
                    "target": build_basic_type(OpenApiTypes.STR),
                    "token": build_basic_type(OpenApiTypes.STR),
                    "captcha_key": build_basic_type(OpenApiTypes.STR),
                    "captcha_code": build_basic_type(OpenApiTypes.STR),
                }
            )
        ),
        responses=get_default_response_schema(
            {
                "data": build_object_type(
                    properties={
                        "verify_token": build_basic_type(OpenApiTypes.STR),
                        "extra": build_basic_type(OpenApiTypes.ANY),
                    }
                )
            }
        ),
    ),
)
class SendVerifyCodeAPIView(GenericAPIView):
    """获取验证码配置"""

    permission_classes: list[type] = []
    authentication_classes: list[type] = []

    @staticmethod
    def prepare_code_data(username: Any) -> Any:
        subject = _("Verify code")
        code = random_string(
            settings.VERIFY_CODE_LENGTH,
            lower=settings.VERIFY_CODE_LOWER_CASE,
            upper=settings.VERIFY_CODE_UPPER_CASE,
            digit=settings.VERIFY_CODE_DIGIT_CASE,
        )
        context = {"username": username, "title": subject, "code": code, "ttl": settings.VERIFY_CODE_TTL}
        message = render_to_string("msg_verify_code.html", context)
        content = {"subject": subject, "message": message}
        return content, code

    @staticmethod
    def get_category(request: Any) -> "VerifyCodeCategory | None":
        """按 category 取场景表项；未知/缺失 category 返回 None（由调用方给可读错误）。"""
        return CATEGORIES.get(request.query_params.get("category"))

    def get(self, request: Any) -> Any:
        category = self.get_category(request)
        if category is None:
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))
        return ApiResponse(data=category.config(request))

    def get_throttles(self) -> Any:
        """IP 维度专用限流只打在发送动作上（GET 配置读取便宜且页面加载必调）；
        保留默认链（全局匿名限流），发送请求在其上叠加更严专用档。"""
        throttles = super().get_throttles()
        if self.request.method.upper() == "POST":
            throttles.append(VerifyCodeThrottle())
        return throttles

    def post(self, request: Any) -> Any:
        """发送验证码"""
        category = self.get_category(request)
        if category is None:
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))
        config = category.config(request)
        if not config.get("access"):
            return ApiResponse(code=1001, detail=_("Forbidden send verification code"))

        form_type = request.data.get("form_type")
        target = request.data.get("target")

        # 表单类型仅限有真实投递通道者（phone/email）：username 类型无投递通道，
        # 不再提供——账号密码登录由 /api/identity/login/basic 承载
        form_types = []
        if config.get("sms"):
            form_types.append("phone")
        if config.get("email"):
            form_types.append("email")

        if not target or form_type not in form_types:
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))

        client_id, token = check_token_and_captcha(request, config.get("token"), config.get("captcha"))

        if config.get("encrypted"):
            target = AESCipherV2(token).decrypt(target)

        query_key = form_type
        if form_type == "phone":
            form_type = "sms"

        ipaddr = get_request_ip(request)

        check_is_block(target, ipaddr, login_block=SendVerifyCodeBlockUtil)

        SendVerifyCodeBlockUtil(target, ipaddr).incr_failed_count()

        try:
            username, extra, should_send = category.check(request, form_type, query_key, target)
        except APIException as e:
            # 业务校验失败（ValidateError 等），异常文案本身面向用户
            LoginIpBlockUtil(ipaddr).set_block_if_need()
            return ApiResponse(code=1001, detail=str(e.detail))
        except Exception:
            # 未预期异常不向客户端暴露内部细节
            logger.exception("Send verify code check failed")
            LoginIpBlockUtil(ipaddr).set_block_if_need()
            return ApiResponse(code=1001, detail=_("Operation failed. Abnormal data"))

        # 仅真实投递通道（phone/email）：验证码随真实下发，响应不回显
        if should_send:
            try:
                content, code = self.prepare_code_data(username)
                SendAndVerifyCodeUtil(target, code, backend=form_type, **content).gen_and_send_async()
            except ValueError as e:
                # 发送工具抛出的 ValueError 为业务文案
                logger.warning("Send verify code failed: %s", e)
                return ApiResponse(code=1002, detail=str(e))
        else:
            # 防枚举：目标不存在时不发送，但支付同等的模板渲染开销（响应时间对齐），
            # 响应文案/结构与其他请求完全一致（后续验证码校验必然失败）
            self.prepare_code_data(username or target)
        cache_data = {"target": target, "form_type": form_type, "query_key": query_key, "extra": extra}
        verify_token = TokenTempCache.generate_cache_token(settings.VERIFY_CODE_TTL, cache_data)
        data = {"verify_token": verify_token, "extra": extra}

        return ApiResponse(data=data, detail=_("The verification code has been sent"))
