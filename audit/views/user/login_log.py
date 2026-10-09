#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : login_log
# author : ly_13
# date : 8/11/2024
from typing import Any

from rest_framework.mixins import ListModelMixin
from rest_framework.request import Request
from rest_framework.viewsets import GenericViewSet

from audit.models import UserLoginLog
from audit.serializers.log import UserLoginLogSerializer
from common.core.modelset import SearchColumnsAction
from common.core.response import ApiResponse


class UserLoginLogViewSet(ListModelMixin, SearchColumnsAction, GenericViewSet):
    """用户登录日志"""

    queryset = UserLoginLog.objects.all()
    serializer_class = UserLoginLogSerializer

    ordering_fields = ["created_time"]

    def get_queryset(self) -> Any:
        return self.queryset.filter(creator=self.request.user)

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Any:
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(data=data)
