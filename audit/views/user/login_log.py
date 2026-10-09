#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : login_log
# author : ly_13
# date : 8/11/2024
from typing import Any

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from rest_framework.mixins import ListModelMixin
from rest_framework.request import Request
from rest_framework.viewsets import GenericViewSet

from audit.models import UserLoginLog
from audit.serializers.log import UserLoginLogSerializer
from common.core.filter import BaseFilterSet
from common.core.modelset import SearchColumnsAction, SearchFieldsAction
from common.core.response import ApiResponse


class UserLoginLogFilter(BaseFilterSet):
    """个人登录日志筛选：安全自查场景（按结果 / 方式 / 来源环境定位一次异常登录）。

    字段面同时是搜索区元数据源（SearchFieldsAction 按 filterset 生成搜索字段），
    缺 filterset 时搜索区只有按钮没有字段。
    """

    status = filters.BooleanFilter(field_name="status", label=_("Login status"))
    login_type = filters.ChoiceFilter(
        field_name="login_type", choices=UserLoginLog.LoginTypeChoices.choices, label=_("Login type")
    )
    ipaddress = filters.CharFilter(field_name="ipaddress", lookup_expr="icontains", label=_("IpAddress"))
    city = filters.CharFilter(field_name="city", lookup_expr="icontains", label=_("Login city"))
    browser = filters.CharFilter(field_name="browser", lookup_expr="icontains", label=_("Browser"))
    system = filters.CharFilter(field_name="system", lookup_expr="icontains", label=_("System"))

    class Meta:
        model = UserLoginLog
        fields = ["status", "login_type", "ipaddress", "city", "browser", "system"]


class UserLoginLogViewSet(ListModelMixin, SearchFieldsAction, SearchColumnsAction, GenericViewSet):
    """用户登录日志（个人安全日志：视图内限定 creator=当前用户）"""

    queryset = UserLoginLog.objects.all()
    serializer_class = UserLoginLogSerializer

    filterset_class = UserLoginLogFilter
    ordering_fields = ["created_time"]

    def get_queryset(self) -> Any:
        return self.queryset.filter(creator=self.request.user)

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Any:
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(data=data)
