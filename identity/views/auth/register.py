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
from common.base.crypto import AESCipherV2
from common.cache.lock import ReentrantLock
from common.core.response import ApiResponse
from common.core.throttle import RegisterThrottle
from common.swagger.utils import get_default_response_schema
from identity.models import DeptInfo, UserInfo
from identity.services.token_issue_login import issue_session_token
from identity.utils.auth import save_login_log, verify_sms_email_code
from settings.services import RegisterBlockUtil, check_leak_password, check_password_rules, record_password_hash


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
        password = request.data.get("password")
        if not password:
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))

        if settings.SECURITY_REGISTER_ENCRYPTED_ENABLED:
            password = AESCipherV2(verify_token).decrypt(password)

        if not check_password_rules(password):
            return ApiResponse(code=1001, detail=_("Password does not match security rules"))
        if check_leak_password(password):
            return ApiResponse(code=1001, detail=_("Password has been leaked, please change to another one"))

        username = target
        default = {query_key: target}
        if query_key == "username":
            default = {}

        # 防并发注册导致手机/邮箱/用户名重复：token 化锁安全释放（可重入、事务外立即释放）
        with ReentrantLock("register_user", timeout=10):
            # username 在 DB 层全局唯一，回收站中的同名用户仍占用唯一约束；
            # 必须走 all_objects 校验，否则软删除同名用户会让 create 抛 IntegrityError 500
            # （与 UserSerializer.validate_username 口径保持一致）
            if UserInfo.all_objects.filter(**{query_key: target}).exists():
                return ApiResponse(code=1002, detail=_("The account already exists, please try another one"))
            user = UserInfo.objects.create_user(username=username, password=password, nickname=username, **default)

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

        # 注册即登录：登记会话并绑定 sid claim（失败不影响注册主流程）；last_login 由签发函数统一刷新
        result = issue_session_token(request, user, UserLoginLog.LoginTypeChoices.USERNAME)
        request.user = user
        save_login_log(request)
        return ApiResponse(data=result)
