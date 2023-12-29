#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : user
# author : ly_13
# date : 6/16/2023
import logging

from django_filters import rest_framework as filters
from rest_framework.decorators import action

from common.base.utils import get_choices_dict
from common.core.filter import get_filter_queryset
from common.core.modelset import BaseModelSet, UploadFileAction
from common.core.response import ApiResponse
from system.models import UserInfo, DeptInfo
from system.utils import notify
from system.utils.modelset import ChangeRolePermissionAction
from system.utils.serializer import UserSerializer

logger = logging.getLogger(__name__)


class UserFilter(filters.FilterSet):
    username = filters.CharFilter(field_name='username', lookup_expr='icontains')
    nickname = filters.CharFilter(field_name='nickname', lookup_expr='icontains')
    mobile = filters.CharFilter(field_name='mobile', lookup_expr='icontains')
    pk = filters.NumberFilter(field_name='id')
    dept = filters.NumberFilter(field_name='dept')

    class Meta:
        model = UserInfo
        fields = ['email', 'is_active', 'gender', 'pk', 'mode_type']


class UserView(BaseModelSet, UploadFileAction, ChangeRolePermissionAction):
    FILE_UPLOAD_FIELD = 'avatar'
    queryset = UserInfo.objects.all()
    serializer_class = UserSerializer

    ordering_fields = ['date_joined', 'last_login', 'created_time']
    filterset_class = UserFilter

    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, choices_dict=get_choices_dict(UserInfo.GenderChoices.choices),
                           mode_choices=get_choices_dict(DeptInfo.ModeChoices.choices))

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        password = request.data.get('password')
        if password:
            valid_data = serializer.data
            # roles = valid_data.pop('roles')
            valid_data.pop('roles_info', None)
            valid_data.pop('rules_info', None)
            valid_data.pop('dept_info', None)
            dept = valid_data.pop('dept', None)
            if dept:
                valid_data['dept'] = get_filter_queryset(DeptInfo.objects.filter(pk=dept), request.user)
            else:
                valid_data['dept'] = request.user.dept
            user = UserInfo.objects.create_user(**valid_data, password=password, creator=request.user,
                                                dept_belong=request.user.dept)
            if user:
                return ApiResponse(detail=f"用户{user.username}添加成功", data=self.get_serializer(user).data)
        return ApiResponse(code=1003, detail="数据异常，用户创建失败")

    def perform_destroy(self, instance):
        if instance.is_superuser:
            raise Exception("超级管理员禁止删除")
        instance.delete()

    @action(methods=['delete'], detail=False, url_path='many-delete')
    def many_delete(self, request, *args, **kwargs):
        self.queryset = self.queryset.filter(is_superuser=False)
        return super().many_delete(request, *args, **kwargs)

    @action(methods=['post'], detail=True)
    def reset_password(self, request, *args, **kwargs):
        instance = self.get_object()
        password = request.data.get('password')
        if instance and password:
            instance.set_password(password)
            instance.modifier = request.user
            instance.save(update_fields=['password', 'modifier'])
            notify.notify_info(users=instance, title="密码重置成功",
                               message="密码被管理员重置成功")
            return ApiResponse()
        return ApiResponse(code=1001, detail='修改失败')
