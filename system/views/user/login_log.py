#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : login_log
# author : ly_13
# date : 8/11/2024
from rest_framework.mixins import ListModelMixin
from rest_framework.viewsets import GenericViewSet

from common.core.modelset import SearchColumnsAction
from common.core.response import ApiResponse
from system.models import UserLoginLog
from system.serializers.log import UserLoginLogSerializer


class UserLoginLogViewSet(ListModelMixin, SearchColumnsAction, GenericViewSet):
    """用户登录日志"""
    queryset = UserLoginLog.objects.all()
    serializer_class = UserLoginLogSerializer

    ordering_fields = ['created_time']

    def get_queryset(self):
        return self.queryset.filter(creator=self.request.user)

    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(data=data)
