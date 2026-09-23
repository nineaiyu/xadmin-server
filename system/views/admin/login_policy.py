#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""登录访问策略管理：策略 CRUD + 命中预演。

「保存前预演」是防误配置的关键：给出样例用户 / IP / 时间即可看到逐条策略的
匹配结果与最终判定，避免「一条 reject 把全员挡在门外」。
"""

from datetime import datetime

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils.request import get_request_ip
from system.models import LoginAccessPolicy, UserInfo
from system.serializers.security import LoginAccessPolicySerializer
from system.utils.login_policy import preview_login_policy


class LoginAccessPolicyFilter(BaseFilterSet):
    class Meta:
        model = LoginAccessPolicy
        fields = ["name", "is_active", "target_type", "action"]


class LoginAccessPolicyViewSet(BaseModelSet):
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
                description="命中预演：用户（用户名或 pk）/ IP / 时间（ISO 格式，默认当前）",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="preview")
    def preview(self, request, *args, **kwargs):
        """登录策略命中预演"""
        username = str(request.data.get("username") or "").strip()
        user = None
        if username:
            user = UserInfo.objects.filter(username=username).first()
            if user is None:
                user = UserInfo.objects.filter(pk=username).first()
        if user is None:
            user = request.user
        ip = str(request.data.get("ip") or "").strip() or get_request_ip(request)
        when = timezone.localtime()
        raw_when = str(request.data.get("when") or "").strip()
        if raw_when:
            try:
                when = timezone.localtime(datetime.fromisoformat(raw_when))
            except ValueError:
                return ApiResponse(code=1004, detail=_("Invalid time format"))
        result = preview_login_policy(user, ip, when)
        result.update({"username": user.username, "ip": ip, "when": when.isoformat()})
        return ApiResponse(data=result)
