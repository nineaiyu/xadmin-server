#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : role
# author : ly_13
# date : 6/19/2023

from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, ImportExportDataAction, RecycleBinAction
from common.utils import get_logger
from system.builtin import BUILTIN_ROLE_CODES
from system.models import UserRole
from system.serializers.role import RoleSerializer, ListRoleSerializer
from system.utils.modelset import RolePreviewAction

logger = get_logger(__name__)


class RoleFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")
    code = filters.CharFilter(field_name="code", lookup_expr="icontains")

    class Meta:
        model = UserRole
        fields = ["name", "code", "is_active", "description", "builtin"]


class RoleViewSet(RecycleBinAction, BaseModelSet, ImportExportDataAction, RolePreviewAction):
    """角色"""

    queryset = UserRole.objects.all()
    serializer_class = RoleSerializer
    list_serializer_class = ListRoleSerializer
    ordering_fields = ["updated_time", "name", "created_time"]
    filterset_class = RoleFilter

    def get_queryset(self):
        # 内置角色（builtin）禁止删除：代码与治理配置按 code 引用，误删会让
        # 审批人角色等配置凭空失效（同内置字典 is_locked 保护口径）；批量删除
        # 同样基于 get_queryset 收口
        if self.action == "destroy":
            return super().get_queryset().exclude(code__in=BUILTIN_ROLE_CODES)
        return super().get_queryset()

    def get_recycle_purge_queryset(self, pks):
        # 回收站物理清除走 all_objects：显式排除内置角色
        return super().get_recycle_purge_queryset(pks).exclude(code__in=BUILTIN_ROLE_CODES)
