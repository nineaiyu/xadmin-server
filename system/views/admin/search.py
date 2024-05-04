#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : search
# author : ly_13
# date : 3/1/2024
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import action

from common.core.modelset import OnlyListModelSet
from common.core.pagination import DynamicPageNumber
from common.core.response import ApiResponse
from system.models import UserInfo, UserRole, DeptInfo, Menu, OperationLog
from system.utils.serializer import UserSerializer, ListRoleSerializer, DeptSerializer, MenuSerializer
from system.views.admin.dept import DeptFilter
from system.views.admin.menu import MenuFilter
from system.views.admin.role import RoleFilter
from system.views.admin.user import UserFilter


class SearchDataView(OnlyListModelSet):
    """简易搜索数据"""
    serializer_class = UserSerializer
    queryset = OperationLog.objects.none()
    filterset_class = None
    ordering_fields = ['updated_time', 'created_time']

    @swagger_auto_schema(auto_schema=None)
    def list(self, request, *args, **kwargs):
        return ApiResponse(code=1001)

    @action(methods=['get'], detail=False, filterset_class=UserFilter, serializer_class=UserSerializer,
            queryset=UserInfo.objects.all(), ordering_fields=['date_joined', 'last_login', 'created_time'])
    def users(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @action(methods=['get'], detail=False, filterset_class=DeptFilter, serializer_class=DeptSerializer,
            queryset=DeptInfo.objects.all(), ordering_fields=['created_time', 'rank'],
            pagination_class=DynamicPageNumber(1000))
    def depts(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @action(methods=['get'], detail=False, filterset_class=RoleFilter, serializer_class=ListRoleSerializer,
            queryset=UserRole.objects.all(), ordering_fields=['updated_time', 'name', 'created_time', 'pk'])
    def roles(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @action(methods=['get'], detail=False, filterset_class=MenuFilter, serializer_class=MenuSerializer,
            queryset=Menu.objects.all(), ordering_fields=['updated_time', 'name', 'created_time', 'rank'],
            pagination_class=DynamicPageNumber(1000))
    def menus(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)
