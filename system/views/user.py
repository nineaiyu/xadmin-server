#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : user
# author : ly_13
# date : 6/16/2023
import logging

from django.db.models import FileField
from django_filters import rest_framework as filters
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter

from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse
from system.models import UserInfo
from system.utils import notify
from system.utils.serializer import UserSerializer

logger = logging.getLogger(__name__)


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
    serializer_class = UserSerializer

    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['date_joined', 'last_login']
    filterset_class = UserFilter

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        password = request.data.get('password')
        if password:
            valid_data = serializer.data
            # roles = valid_data.pop('roles')
            valid_data.pop('roles_info')
            user = UserInfo.objects.create_user(**valid_data, password=password)
            # user.roles.set(UserRole.objects.filter(pk__in=roles))
            if user:
                return ApiResponse(detail=f"用户{user.username}添加成功", data=self.get_serializer(user).data)
        return ApiResponse(code=1003, detail="数据异常，用户创建失败")

    def perform_destroy(self, instance):
        if instance.is_superuser:
            raise Exception("超级管理员禁止删除")
        instance.delete()

    @action(methods=['delete'], detail=False)
    def many_delete(self, request, *args, **kwargs):
        self.queryset = self.queryset.filter(is_superuser=False)
        return super().many_delete(request, *args, **kwargs)

    @action(methods=['post'], detail=False)
    def upload(self, request, *args, **kwargs):
        files = request.FILES.getlist('file', [])
        uid = request.data.get('uid')
        if uid:
            user_obj = UserInfo.objects.filter(pk=uid).first()
            if user_obj:
                file_obj = files[0]
                try:
                    file_type = file_obj.name.split(".")[-1]
                    if file_type not in ['png', 'jpeg', 'jpg', 'gif']:
                        logger.error(f"user:{request.user} upload file type error file:{file_obj.name}")
                        raise
                    if file_obj.size > 1024 * 1024 * 3:
                        return ApiResponse(code=1003, detail="图片大小不能超过3兆")
                except Exception as e:
                    logger.error(f"user:{request.user} upload file type error Exception:{e}")
                    return ApiResponse(code=1002, detail="错误的图片类型")
                delete_avatar_name = None
                if user_obj.avatar:
                    delete_avatar_name = user_obj.avatar.name
                user_obj.avatar = file_obj
                user_obj.save(update_fields=['avatar'])
                if delete_avatar_name:
                    FileField(name=delete_avatar_name).storage.delete(delete_avatar_name)
                return ApiResponse()
        return ApiResponse(code=1004, detail="数据异常")

    @action(methods=['post'], detail=False)
    def reset_password(self, request, *args, **kwargs):
        uid = request.data.get('uid')
        password = request.data.get('password')
        if uid and password:
            user_obj = UserInfo.objects.filter(pk=uid).first()
            if user_obj:
                user_obj.set_password(password)
                user_obj.save()
                notify.notify_info(users=user_obj, title="密码重置成功",
                                   message="密码被管理员重置成功")
                return ApiResponse()
        return ApiResponse(code=1001, detail='修改失败')
