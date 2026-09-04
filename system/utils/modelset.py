#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : modelset
# author : ly_13
# date : 12/24/2023
from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_object_type, build_basic_type, build_array_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.decorators import action

from common.core.config import SysConfig, UserConfig
from common.core.filter import get_filter_queryset
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models import UserRole, DataPermission, SystemConfig


class ChangeRolePermissionAction(object):

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                required=['roles', 'rules', 'mode_type'],
                properties={
                    'roles': build_array_type(build_basic_type(OpenApiTypes.STR)),
                    'rules': build_array_type(build_basic_type(OpenApiTypes.STR)),
                    'mode_type': build_basic_type(OpenApiTypes.NUMBER),
                }
            )
        ),
        responses=get_default_response_schema()
    )
    @action(methods=['post'], detail=True)
    def empower(self, request, *args, **kwargs):
        """给{cls}分配角色-数据权限"""
        instance = self.get_object()
        roles = request.data.get('roles')
        rules = request.data.get('rules')
        mode_type = request.data.get('mode_type', instance.mode_type)
        if isinstance(mode_type, dict):
            mode_type = mode_type.get('value')
        if roles is not None or rules is not None:
            if roles is not None:
                instance.roles.set(
                    get_filter_queryset(UserRole.objects.filter(pk__in=[role.get('pk') for role in roles]),
                                        request.user).all())
            if rules is not None:
                instance.mode_type = mode_type
                instance.modifier = request.user
                instance.save(update_fields=['mode_type', 'modifier'])
                # instance.rules.set(get_filter_queryset(DataPermission.objects.filter(pk__in=rules), request.user).all())
                # 数据权限是部门进行并查询过滤，可以直接进行查询
                instance.rules.set(DataPermission.objects.filter(pk__in=[rule.get('pk') for rule in rules]).all())
            return ApiResponse()
        return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))


class InvalidConfigCacheAction(object):

    @extend_schema(request=None, responses=get_default_response_schema())
    @action(methods=['post'], detail=True)
    def invalid(self, request, *args, **kwargs):
        """使{cls}缓存失效"""
        instance = self.get_object()

        if isinstance(instance, SystemConfig):
            SysConfig.invalid_config_cache(key=instance.key)
            owner = '*'
        else:
            owner = instance.owner
        UserConfig(owner).invalid_config_cache(key=instance.key)
        return ApiResponse()


class AnnotateUserCountMixin(object):
    """
    部门 user_count 预聚合：列表/详情/导出会逐行序列化该字段，不加聚合时为每行一次 COUNT。

    反向查询名固定为 dept_query —— UserInfo.dept 显式声明了 related_query_name，
    覆盖了默认的模型名小写，写成 userinfo 会抛 FieldError。

    仅在会逐行序列化的 action 生效，避免 annotate 影响 update/delete 等写操作。
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, 'action', None) in getattr(self, 'auto_prefetch_actions', ()):
            # annotate 产生 GROUP BY 后 Django 不再套用 Meta.ordering，需显式补回，
            # 否则分页顺序不稳定；前端传 ordering 时 OrderingFilter 会在其后覆盖
            ordering = queryset.query.order_by or queryset.model._meta.ordering
            queryset = queryset.annotate(user_count=Count('dept_query'))
            if ordering:
                queryset = queryset.order_by(*ordering)
        return queryset
