#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : register
# author : ly_13
# date : 8/8/2024
from typing import Any

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.generics import GenericAPIView

from audit.services import UserLoginLog
from common.cache.lock import ReentrantLock
from common.core.response import ApiResponse
from common.core.throttle import RegisterThrottle
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from identity.models import DeptInfo, UserInfo
from identity.services.auth_login import complete_login
from identity.services.token_issue_login import issue_session_token
from identity.utils.auth import decrypt_submitted, verify_sms_email_code

logger = get_logger(__name__)

from settings.services import (
    RegisterBlockUtil,
    check_leak_password,
    check_password_rules,
    email_service_ready,
    record_password_hash,
    sms_service_ready,
)


class RegisterViewAPIView(GenericAPIView):
    """用户注册"""

    permission_classes: list[type] = []
    authentication_classes: list[type] = []
    throttle_classes = [RegisterThrottle]

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "channel": build_basic_type(OpenApiTypes.STR),
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
        """注册账户"""
        if not settings.SECURITY_REGISTER_ACCESS_ENABLED:
            return ApiResponse(code=1001, detail=_("Registration forbidden"))

        channel = request.data.get("channel", "default")

        query_key, target, verify_token = verify_sms_email_code(request, RegisterBlockUtil, scenes=("register",))
        # 提交侧复核通道开关：发码后关闭邮箱/短信注册时，已发出的验证码也不能继续注册。
        channel_ready = {
            "email": settings.SECURITY_REGISTER_BY_EMAIL_ENABLED and email_service_ready(),
            "phone": settings.SECURITY_REGISTER_BY_SMS_ENABLED and sms_service_ready(),
            "sms": settings.SECURITY_REGISTER_BY_SMS_ENABLED and sms_service_ready(),
        }.get(query_key)
        if channel_ready is False:
            return ApiResponse(code=1001, detail=_("Registration forbidden"))
        password = request.data.get("password")
        if not password:
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))

        if settings.SECURITY_REGISTER_ENCRYPTED_ENABLED:
            password = decrypt_submitted(password, verify_token)

        if not check_password_rules(password):
            return ApiResponse(code=1001, detail=_("Password does not match security rules"))
        if check_leak_password(password):
            return ApiResponse(code=1001, detail=_("Password has been leaked, please change to another one"))

        username = target
        default = {query_key: target}
        if query_key == "username":
            default = {}

        # 防并发注册导致手机/邮箱/用户名重复：锁到事务提交后释放，避免第二个请求
        # 在第一个请求提交前读到「不存在」；等待上限 5s，锁被长期占用时退化为
        # 唯一约束兜底（不能返回 500）
        from django.db import IntegrityError

        from common.cache.lock import LockNotOwnedError

        def _create_registered_user() -> Any:
            # 唯一性口径统一为「活跃行」：软删账号的邮箱/用户名可复用（DB 部分唯一索引同口径）
            if UserInfo.objects.filter(**{query_key: target}).exists():
                return ApiResponse(code=1002, detail=_("The account already exists, please try another one"))
            try:
                return UserInfo.objects.create_user(username=username, password=password, nickname=username, **default)
            except IntegrityError:
                return ApiResponse(code=1002, detail=_("The account already exists, please try another one"))

        try:
            with ReentrantLock("register_user", timeout=10, blocking_timeout=5, release_on_commit=True):
                user = _create_registered_user()
        except LockNotOwnedError:
            logger.warning("register lock busy, fallback to unique constraint")
            user = _create_registered_user()
        if isinstance(user, ApiResponse):
            return user

        # 注册即留存首条密码历史（建号链路统一口径）；后续改密的「最近 N 次不可复用」覆盖初始密码
        record_password_hash(user, user.password)

        if channel and user:
            # 仅按 channel 精确匹配部门编码；不再回落到任意第一个 auto_bind 部门，
            # 避免未匹配渠道的注册用户被塞进不确定部门、污染数据权限归属
            dept = DeptInfo.objects.filter(is_active=True, auto_bind=True, code=channel).first()
            if dept:
                user.dept = dept
                user.creator = user
                user.dept_belong = dept
                user.save(update_fields=["dept_belong", "dept", "creator"])

        # 注册即登录同样经唯一收口：登录开关关闭 / 访问策略拒绝时不签发 token，
        # 账号保留供管理员处理；策略要求 MFA 时返回与普通登录一致的挑战载荷。
        mfa_response = complete_login(request, user, login_type=UserLoginLog.LoginTypeChoices.USERNAME)
        if mfa_response:
            return mfa_response
        # 会话登记并绑定 sid claim；last_login 由签发函数统一刷新
        result = issue_session_token(request, user, UserLoginLog.LoginTypeChoices.USERNAME)
        return ApiResponse(data=result)
