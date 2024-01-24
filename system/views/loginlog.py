#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : loginlog
# author : ly_13
# date : 1/3/2024


from django_filters import rest_framework as filters

from common.base.utils import get_choices_dict
from common.core.modelset import ListDeleteModelSet
from common.core.response import ApiResponse
from system.models import UserLoginLog
from system.utils.serializer import UserLoginLogSerializer


class UserLoginLogFilter(filters.FilterSet):
    ipaddress = filters.CharFilter(field_name='ipaddress', lookup_expr='icontains')
    system = filters.CharFilter(field_name='system', lookup_expr='icontains')
    browser = filters.CharFilter(field_name='browser', lookup_expr='icontains')
    agent = filters.CharFilter(field_name='agent', lookup_expr='icontains')
    creator_id = filters.NumberFilter(field_name='creator__id')

    class Meta:
        model = UserLoginLog
        fields = ['creator_id', 'login_type']


class UserLoginLogView(ListDeleteModelSet):
    queryset = UserLoginLog.objects.all()
    serializer_class = UserLoginLogSerializer

    ordering_fields = ['created_time']
    filterset_class = UserLoginLogFilter

    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, choices_dict=get_choices_dict(UserLoginLog.LoginTypeChoices.choices))
