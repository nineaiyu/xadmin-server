#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : modelset
# author : ly_13
# date : 12/24/2023
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import action

from common.core.config import SysConfig, UserConfig
from common.core.filter import get_filter_queryset
from common.core.response import ApiResponse
from system.models import UserRole, DataPermission, SystemConfig, ModeTypeAbstract


class ChangeRolePermissionAction(object):
    def get_object(self):
        raise NotImplementedError('get_object must be overridden')

    @swagger_auto_schema(request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=['roles', 'rules', 'mode_type'],
        properties={'roles': openapi.Schema(description='角色信息', type=openapi.TYPE_ARRAY,
                                            items=openapi.Schema(type=openapi.TYPE_STRING, )),
                    'rules': openapi.Schema(description='数据权限', type=openapi.TYPE_ARRAY,
                                            items=openapi.Schema(type=openapi.TYPE_STRING, )),
                    'mode_type': openapi.Schema(description='权限模式', type=openapi.TYPE_NUMBER,
                                                default=ModeTypeAbstract.ModeChoices.OR)}
    ), operation_description='分配角色-数据权限')
    @action(methods=['post'], detail=True)
    def empower(self, request, *args, **kwargs):
        instance = self.get_object()
        roles = request.data.get('roles')
        rules = request.data.get('rules')
        mode_type = request.data.get('mode_type', instance.mode_type)
        if isinstance(mode_type, dict):
            mode_type = mode_type.get('value')
        if roles is not None or rules is not None:
            if roles is not None:
                instance.roles.set(get_filter_queryset(UserRole.objects.filter(pk__in=roles), request.user).all())
            if rules is not None:
                instance.mode_type = mode_type
                instance.modifier = request.user
                instance.save(update_fields=['mode_type', 'modifier'])
                # instance.rules.set(get_filter_queryset(DataPermission.objects.filter(pk__in=rules), request.user).all())
                # 数据权限是部门进行并查询过滤，可以直接进行查询
                instance.rules.set(DataPermission.objects.filter(pk__in=rules).all())
            return ApiResponse(detail="操作成功")
        return ApiResponse(code=1004, detail="数据异常")


class InvalidConfigCacheAction(object):
    def get_object(self):
        raise NotImplementedError('get_object must be overridden')

    @swagger_auto_schema(operation_description="使配置值缓存失效", ignore_params=True)
    @action(methods=['post'], detail=True)
    def invalid(self, request, *args, **kwargs):
        instance = self.get_object()

        if isinstance(instance, SystemConfig):
            SysConfig.invalid_config_cache(key=instance.key)
            owner = '*'
        else:
            owner = instance.owner
        UserConfig(owner).invalid_config_cache(key=instance.key)
        return ApiResponse(detail="操作成功")
