#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : user
# author : ly_13
# date : 6/16/2023

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_object_type, build_array_type, build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.decorators import action

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, UploadFileAction, ImportExportDataAction
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from message.utils import send_logout_msg
from notifications.message import SiteMessageUtil
from settings.utils.security import LoginBlockUtil
from system.models import UserInfo
from system.serializers.user import UserSerializer, ResetPasswordSerializer
from system.utils.modelset import ChangeRolePermissionAction

logger = get_logger(__name__)


class UserFilter(BaseFilterSet):
    username = filters.CharFilter(field_name='username', lookup_expr='icontains')
    nickname = filters.CharFilter(field_name='nickname', lookup_expr='icontains')
    phone = filters.CharFilter(field_name='phone', lookup_expr='icontains')

    class Meta:
        model = UserInfo
        fields = ['username', 'nickname', 'phone', 'email', 'is_active', 'gender', 'pk', 'mode_type', 'dept']


class UserViewSet(BaseModelSet, UploadFileAction, ChangeRolePermissionAction, ImportExportDataAction):
    """用户"""
    FILE_UPLOAD_FIELD = 'avatar'
    queryset = UserInfo.objects.all()
    serializer_class = UserSerializer

    ordering_fields = ['date_joined', 'last_login', 'created_time']
    filterset_class = UserFilter

    # export_as_zip = True  导出zip压缩包，密码是用户名

    def perform_destroy(self, instance):
        if instance.is_superuser:
            raise Exception(_("The super administrator disallows deletion"))
        return instance.delete()

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={'pks': build_array_type(build_basic_type(OpenApiTypes.STR))},
                required=['pks'],
                description="主键列表"
            )
        ),
        responses=get_default_response_schema()
    )
    @action(methods=['post'], detail=False, url_path='batch-destroy')
    def batch_destroy(self, request, *args, **kwargs):
        """批量删除{cls}"""
        self.queryset = self.queryset.filter(is_superuser=False)
        return super().batch_destroy(request, *args, **kwargs)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=['post'], detail=True, url_path='reset-password', serializer_class=ResetPasswordSerializer)
    def reset_password(self, request, *args, **kwargs):
        """重置用户密码"""
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        SiteMessageUtil.notify_error(users=instance, title="密码重置成功", message="密码被管理员重置成功")
        return ApiResponse()

    @extend_schema(responses=get_default_response_schema(), request=None)
    @action(methods=["post"], detail=True)
    def unblock(self, request, *args, **kwargs):
        """解禁用户"""
        instance = self.get_object()
        LoginBlockUtil.unblock_user(instance.username)
        return ApiResponse()

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={'channel_names': build_array_type(build_basic_type(OpenApiTypes.STR))},
                required=['channel_names'],
                description="列表"
            )
        ),
        responses=get_default_response_schema()
    )
    @action(methods=["post"], detail=True)
    def logout(self, request, *args, **kwargs):
        """强退用户"""
        instance = self.get_object()
        channel_names = request.data.get('channel_names', [])
        send_logout_msg(instance.pk, channel_names)
        return ApiResponse()
