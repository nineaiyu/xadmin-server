#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : loginlog
# author : ly_13
# date : 1/3/2024


from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from audit.models import UserLoginLog
from audit.serializers.log import LoginLogSerializer
from common.core.filter import BaseFilterSet, PkMultipleFilter
from common.core.modelset import OnlyExportDataAction, OnlyListModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from message.services import send_logout_msg


class LoginLogFilter(BaseFilterSet):
    ipaddress = filters.CharFilter(field_name="ipaddress", lookup_expr="icontains")
    city = filters.CharFilter(field_name="city", lookup_expr="icontains")
    system = filters.CharFilter(field_name="system", lookup_expr="icontains")
    agent = filters.CharFilter(field_name="agent", lookup_expr="icontains")
    creator_id = PkMultipleFilter(input_type="api-search-user")

    class Meta:
        model = UserLoginLog
        fields = ["login_type", "ipaddress", "city", "system", "creator_id", "status", "agent", "created_time"]


class LoginLogViewSet(OnlyListModelSet, OnlyExportDataAction):
    """登录日志（只读 + 导出 + 强退）

    审计痕迹不可经 API 抹除：与操作日志（OperationLogViewSet）同口径
    不提供删除 / 批量删除端点，避免「登录成功记录可删」与只读操作日志不对称。
    强退是会话管理动作（不影响日志留存），保留。
    """

    queryset = UserLoginLog.objects.all()
    serializer_class = LoginLogSerializer

    ordering_fields = ["created_time"]
    filterset_class = LoginLogFilter

    @extend_schema(responses=get_default_response_schema(), request=None)
    @action(methods=["post"], detail=True)
    def logout(self, request, *args, **kwargs):
        """强退用户"""
        instance = self.get_object()
        # creator / channel_name 可能为空（历史日志、系统记录）：空值返回可读错误，避免 AttributeError 500
        if not instance.creator_id or not instance.channel_name:
            return ApiResponse(code=400, detail=_("This login record cannot be forcibly logged out"))
        send_logout_msg(instance.creator_id, [instance.channel_name])
        return ApiResponse()
