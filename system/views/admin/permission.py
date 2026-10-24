#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : permission
# author : ly_13
# date : 6/16/2023
from typing import Any

from django.db.models import Count
from django_filters import rest_framework as filters

from common.core.approval import ApprovalRequired
from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, ImpactPreviewAction, ImportExportDataAction, RecycleBinAction
from common.utils import get_logger
from system.models import DataPermission
from system.serializers.permission import DataPermissionSerializer

logger = get_logger(__name__)


class DataPermissionFilter(BaseFilterSet):
    pk = filters.UUIDFilter(field_name="id")
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = DataPermission
        fields = ["pk", "name", "mode_type", "is_active", "description"]


class DataPermissionViewSet(BaseModelSet, ImportExportDataAction, RecycleBinAction, ImpactPreviewAction):
    """数据权限"""

    queryset = DataPermission.objects.all()
    serializer_class = DataPermissionSerializer
    ordering_fields = ["created_time"]
    filterset_class = DataPermissionFilter

    @ApprovalRequired()
    def destroy(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """删除{cls}数据

        高危：行为变更立即影响被授权用户可见数据，可经 APPROVAL_REQUIRED_PATHS 纳入审批。
        """
        return super().destroy(request, *args, **kwargs)

    def get_queryset(self) -> Any:
        queryset = super().get_queryset()
        if self.action == "list":
            # 列表页展示生效范围与分配对象统计：注解计数避免逐行 count()；
            # 多个关系注解会相互放大，统一 distinct=True 纠正
            queryset = queryset.annotate(
                scope_menu_count=Count("menu", distinct=True),
                scope_user_count=Count("userinfo", distinct=True),
                scope_dept_count=Count("deptinfo", distinct=True),
            )
        return queryset
