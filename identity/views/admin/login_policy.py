#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""登录访问策略管理：策略 CRUD + 命中预演。

「保存前预演」是防误配置的关键：给出样例用户 / IP / 时间即可看到逐条策略的
匹配结果与最终判定，避免「一条 reject 把全员挡在门外」。
"""

from datetime import datetime
from typing import Any

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, ChoicesAction
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils.request import get_request_ip
from identity.models import LoginAccessPolicy, UserInfo
from identity.serializers.security import LoginAccessPolicySerializer
from identity.utils.login_policy import preview_login_policy


class LoginAccessPolicyFilter(BaseFilterSet):
    class Meta:
        model = LoginAccessPolicy
        fields = ["name", "is_active", "target_type", "action"]


class LoginAccessPolicyViewSet(BaseModelSet, ChoicesAction):
    """登录访问策略"""

    queryset = LoginAccessPolicy.objects.all()
    serializer_class = LoginAccessPolicySerializer
    filterset_class = LoginAccessPolicyFilter
    ordering = ["priority", "created_time"]
    ordering_fields = ["priority", "created_time", "updated_time"]

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "username": build_basic_type(OpenApiTypes.STR),
                    "ip": build_basic_type(OpenApiTypes.STR),
                    "when": build_basic_type(OpenApiTypes.STR),
                },
                description="命中预演：用户（用户名或 pk，查无此用户报错而非回退）/ IP / 时间（ISO 格式，默认当前）",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="preview")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def preview(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """登录策略命中预演"""
        username = str(request.data.get("username") or "").strip()
        user = None
        if username:
            user = UserInfo.objects.filter(username=username).first()
            if user is None:
                try:
                    user = UserInfo.objects.filter(pk=username).first()
                except (ValueError, ValidationError):
                    # pk 为 UUID：非法字符串按查无此用户处理（而非 500）
                    user = None
            if user is None:
                # 静默回退到当前用户会让管理员把别人的预演结果当成目标用户的，
                # 必须显式报错（预演的价值在于准确）
                return ApiResponse(code=1004, detail=_("Sample user not found: {}").format(username))
        if user is None:
            user = request.user
        ip = str(request.data.get("ip") or "").strip() or get_request_ip(request)
        when = timezone.localtime()
        raw_when = str(request.data.get("when") or "").strip()
        if raw_when:
            try:
                parsed = datetime.fromisoformat(raw_when)
            except ValueError:
                return ApiResponse(code=1004, detail=_("Invalid time format"))
            # fromisoformat 对无时区输入产出 naive datetime，而 localtime() 只接受
            # aware datetime——naive 直传会在同一 try 里抛 ValueError，被误报成
            # 「时间格式无效」。按当前时区解释 naive 输入（管理页 datetime picker
            # 传本地墙上时间，语义正确）。
            when = timezone.localtime(parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed))
        result = preview_login_policy(user, ip, when)
        # require_mfa 的预演与真实登录存在已知分叉：真实登录在用户无可用
        # 二次验证方式时降级放行（防自锁）。这里探测该用户是否有可用方式，
        # 供前端提示「require_mfa 不会真的拦住此用户」；探测失败置 None（未知，
        # 前端不提示），不影响预演主体结果。
        try:
            from mfa.services import get_login_mfa_methods

            result["mfa_usable"] = bool(get_login_mfa_methods(user))
        except Exception:  # noqa: BLE001
            result["mfa_usable"] = None
        result.update({"username": user.username, "ip": ip, "when": when.isoformat()})
        return ApiResponse(data=result)
