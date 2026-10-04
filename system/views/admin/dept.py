#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : dept
# author : ly_13
# date : 6/16/2023
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from common.core.approval import ApprovalRequired
from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, BatchPartialUpdateAction, ImpactPreviewAction, ImportExportDataAction
from common.core.pagination import DynamicPageNumber
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from system.models import DeptInfo, UserInfo
from system.serializers.department import DeptManagerAssignSerializer, DeptSerializer
from system.utils.identity.dept_managers import assign_dept_managers
from system.utils.platform.modelset import AnnotateUserCountMixin, ChangeRolePermissionAction, DeptPreviewAction

logger = get_logger(__name__)


class DeptFilter(BaseFilterSet):
    pk = filters.UUIDFilter(field_name="id")
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = DeptInfo
        fields = ["pk", "is_active", "code", "auto_bind", "leader", "name", "description"]


class DeptViewSet(
    AnnotateUserCountMixin,
    BatchPartialUpdateAction,
    BaseModelSet,
    ImpactPreviewAction,
    ChangeRolePermissionAction,
    DeptPreviewAction,
    ImportExportDataAction,
):
    """部门"""

    queryset = DeptInfo.objects.all()
    serializer_class = DeptSerializer
    # 批量更新白名单：批量启停用 / 改主管
    batch_update_fields = ("is_active", "leader")
    pagination_class = DynamicPageNumber(1000)
    ordering_fields = ["created_time", "rank"]
    filterset_class = DeptFilter

    @ApprovalRequired()
    def destroy(self, request, *args, **kwargs):
        """删除{cls}数据（高危：可经 APPROVAL_REQUIRED_PATHS 纳入审批）"""
        return super().destroy(request, *args, **kwargs)

    @ApprovalRequired()
    @action(methods=["post"], detail=False, url_path="batch-destroy")
    def batch_destroy(self, request, *args, **kwargs):
        """批量删除{cls}（高危：与删除同口径纳入审批）"""
        return super().batch_destroy(request, *args, **kwargs)

    @extend_schema(request=DeptManagerAssignSerializer, responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="assign-managers")
    def assign_managers(self, request, *args, **kwargs):
        """部门管理员任命：`{add, remove}` 增量变更（幂等）。

        任命同时装配预置角色与用户级数据权限规则（解任按「不再管理任何部门」回收），
        唯一写口；管理员清单随响应回显。
        """
        dept = self.get_object()
        serializer = DeptManagerAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            managers = assign_dept_managers(
                dept,
                add_pks=serializer.validated_data.get("add") or [],
                remove_pks=serializer.validated_data.get("remove") or [],
                operator=request.user,
            )
        except (DjangoValidationError, ValueError, TypeError):
            # 非法主键形态：按可读参数错误返回（不落 500）
            return ApiResponse(code=1001, detail=_("Invalid manager id"))
        return ApiResponse(data={"managers": managers}, detail=_("Managers updated"))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="user-options")
    def user_options(self, request, *args, **kwargs):
        """管理员候选：按关键字搜索在用用户（≤20 条，仅 pk/用户名/昵称）。

        与选人控件同源（system/utils/identity/user_options.py）；权限与 list 同口径
        （框架 shared_list 注册表，无需新增权限点）。
        """
        from system.utils.identity.user_options import search_user_options

        data = search_user_options(
            keyword=request.query_params.get("keyword", ""),
            pks=request.query_params.get("pks", ""),
        )
        return ApiResponse(data=data)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="managed")
    def managed(self, request, *args, **kwargs):
        """我的管辖：当前用户任管理员的部门（含全部下级）与成员统计（恒定本人范围，只读）。

        部门行附带主管（leader）与管理员清单（managers，含共管同事），供管辖页
        直接展示联系人；user_count 为直属成员数，总体成员数在顶层 user_count。
        """
        direct_pks = list(request.user.managed_depts.filter(is_active=True).values_list("pk", flat=True))
        tree_pks = [str(pk) for pk in DeptInfo.dept_tree_pks(direct_pks)] if direct_pks else []
        rows = (
            DeptInfo.objects.filter(pk__in=tree_pks, is_active=True)
            .annotate(member_count=Count("dept_query"))
            .select_related("leader")
            .prefetch_related("managers")
            .order_by("rank", "name")
        )
        direct_set = {str(pk) for pk in direct_pks}

        def user_ref(user):
            return {"pk": user.pk, "nickname": user.nickname, "username": user.username}

        depts = [
            {
                "pk": row.pk,
                "name": row.name,
                "code": row.code,
                "parent_id": row.parent_id,
                "user_count": row.member_count,
                "is_direct": str(row.pk) in direct_set,
                "leader": user_ref(row.leader) if row.leader else None,
                "managers": [user_ref(m) for m in row.managers.all()],
            }
            for row in rows
        ]
        user_count = UserInfo.objects.filter(dept__in=tree_pks, is_active=True).count() if tree_pks else 0
        return ApiResponse(data={"depts": depts, "dept_count": len(depts), "user_count": user_count})
