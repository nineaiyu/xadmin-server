#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : user
# author : ly_13
# date : 6/16/2023

from django_filters import rest_framework as filters
from rest_framework.filters import OrderingFilter

from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse
from system.models import UserInfo, UserRole
from system.utils.serializer import UserInfoSerializer


class UserFilter(filters.FilterSet):
    username = filters.CharFilter(field_name='username', lookup_expr='icontains')
    nickname = filters.CharFilter(field_name='nickname', lookup_expr='icontains')
    mobile = filters.CharFilter(field_name='mobile', lookup_expr='icontains')
    pk = filters.NumberFilter(field_name='id')

    class Meta:
        model = UserInfo
        fields = ['email', 'is_active', 'sex', 'pk']


class UserView(BaseModelSet):
    queryset = UserInfo.objects.all()
    serializer_class = UserInfoSerializer

    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['date_joined', 'last_login']
    filterset_class = UserFilter

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        password = request.data.get('password')
        if password:
            valid_data = serializer.data
            roles = valid_data.pop('roles')
            valid_data.pop('roles_info')
            user = UserInfo.objects.create_user(**valid_data, password=password)
            user.roles.set(UserRole.objects.filter(pk__in=roles))
            if user:
                return ApiResponse(detail=f"用户{user.username}添加成功", data=self.get_serializer(user).data)
        return ApiResponse(code=1003, detail="数据异常，用户创建失败")

    def update(self, request, *args, **kwargs):
        password = request.data.get('password')
        pk = request.data.get('pk')
        username = request.data.get('username')
        if password and pk and username:
            obj = UserInfo.objects.filter(pk=pk).first()
            if obj:
                obj.set_password(password)
                obj.save(update_fields=["password"])
                return ApiResponse(detail=f"用户{obj.username}密码重置成功", data=self.get_serializer(obj).data)
        else:
            return super().update(request, *args, **kwargs)

    def perform_destroy(self, instance):
        if instance.is_superuser:
            raise Exception("超级管理员禁止删除")
        instance.delete()
