#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : dept
# author : ly_13
# date : 6/16/2023
from django_filters import rest_framework as filters
from rest_framework.decorators import action

from common.core.approval import ApprovalRequired
from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, ImportExportDataAction
from common.core.pagination import DynamicPageNumber
from common.utils import get_logger
from system.models import DeptInfo
from system.serializers.department import DeptSerializer
from system.utils.modelset import AnnotateUserCountMixin, ChangeRolePermissionAction, DeptPreviewAction

logger = get_logger(__name__)


class DeptFilter(BaseFilterSet):
    pk = filters.UUIDFilter(field_name="id")
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = DeptInfo
        fields = ["pk", "is_active", "code", "auto_bind", "leader", "name", "description"]


class DeptViewSet(
    AnnotateUserCountMixin, BaseModelSet, ChangeRolePermissionAction, DeptPreviewAction, ImportExportDataAction
):
    """部门"""

    queryset = DeptInfo.objects.all()
    serializer_class = DeptSerializer
    pagination_class = DynamicPageNumber(1000)
    ordering_fields = ["created_time", "rank"]
    filterset_class = DeptFilter

    @ApprovalRequired()
    def destroy(self, request, *args, **kwargs):
        """删除{cls}数据（高危：可经 APPROVAL_REQUIRED_PATHS 纳入审批，见 ADR-032）"""
        return super().destroy(request, *args, **kwargs)

    @ApprovalRequired()
    @action(methods=["post"], detail=False, url_path="batch-destroy")
    def batch_destroy(self, request, *args, **kwargs):
        """批量删除{cls}（高危：与删除同口径纳入审批）"""
        return super().batch_destroy(request, *args, **kwargs)
