#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : role
# author : ly_13
# date : 6/19/2023
import logging

from django_filters import rest_framework as filters
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter

from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse
from system.models import UserRole, UserInfo
from system.utils.serializer import RoleSerializer

logger = logging.getLogger(__name__)


class RoleFilter(filters.FilterSet):
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')
    description = filters.CharFilter(field_name='description', lookup_expr='icontains')
    code = filters.CharFilter(field_name='code', lookup_expr='icontains')

    class Meta:
        model = UserRole
        fields = ['name', 'is_active']


class RoleView(BaseModelSet):
    queryset = UserRole.objects.all()
    serializer_class = RoleSerializer

    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['updated_time', 'name', 'created_time', 'pk']
    filterset_class = RoleFilter

    @action(methods=['post'], detail=False)
    def empower(self, request, *args, **kwargs):
        uid = request.data.get('uid')
        roles = request.data.get('roles')
        if uid and roles is not None:
            user_obj = UserInfo.objects.filter(pk=uid).first()
            roles_queryset = UserRole.objects.filter(pk__in=roles).all()
            if user_obj:
                user_obj.roles.set(roles_queryset)
                return ApiResponse(detail="操作成功")

        return ApiResponse(code=1004, detail="数据异常")
