#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批中心：审批单查询 / 通过 / 驳回 / 撤回 / 批量通过。

取值域不走通用数据权限：超管可见全部，普通用户可见「我发起的 + 待我审批
（PENDING）+ 我审批过的」，与待审批页签口径一致。
"""

from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.filters import BaseFilterBackend, OrderingFilter
from rest_framework.viewsets import GenericViewSet

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseViewSet, DetailAction, ListAction, SearchColumnsAction, SearchFieldsAction
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from system.models.approval import ApprovalRequest
from system.serializers.approval import ApprovalRequestSerializer
from system.utils.approval import approve_request, can_approve, cancel_request, reject_request

logger = get_logger(__name__)


class ApprovalRequestFilter(BaseFilterSet):
    module = filters.CharFilter(field_name="module", lookup_expr="icontains")
    path = filters.CharFilter(field_name="path", lookup_expr="icontains")

    class Meta:
        model = ApprovalRequest
        fields = ["module", "path", "method", "status", "creator", "created_time"]


class ApprovalScopeFilter(BaseFilterBackend):
    """审批单取值域 + 页签 scope 过滤。

    - scope=pending（待我审批）：PENDING 且我可审批（超管或属审批人集合）
    - scope=mine（我发起的）：creator=self
    - 缺省：我发起的 ∪ 待我审批 ∪ 我审批过的；超管不设限

    注意：本类不继承 DjangoFilterBackend（后者会再次执行 filterset，属重复过滤），
    字段过滤由 filter_backends 里独立的 DjangoFilterBackend 负责。
    """

    def filter_queryset(self, request, queryset, view):
        user = request.user
        if not user or not user.is_authenticated:
            return queryset.none()
        scope = request.query_params.get("scope")
        if scope == "pending":
            return queryset.filter(status=ApprovalRequest.Status.PENDING)
        if scope == "mine":
            return queryset.filter(creator=user)
        if user.is_superuser:
            return queryset
        if can_approve(user):
            return queryset.filter(Q(creator=user) | Q(status=ApprovalRequest.Status.PENDING) | Q(approver=user))
        return queryset.filter(Q(creator=user) | Q(approver=user))


class ApprovalRequestViewSet(
    BaseViewSet,
    ListAction,
    DetailAction,
    SearchFieldsAction,
    SearchColumnsAction,
    GenericViewSet,
):
    """审批中心"""

    queryset = ApprovalRequest.objects.all()
    serializer_class = ApprovalRequestSerializer
    filterset_class = ApprovalRequestFilter
    filter_backends = (DjangoFilterBackend, OrderingFilter, ApprovalScopeFilter)
    ordering = ["-created_time"]
    ordering_fields = ["created_time"]
    select_related_fields = ("creator", "approver")

    def _get_actionable(self, request):
        """取审批单并校验审批权限（超管或审批人集合；申请人不能自审在动作内判断）。"""
        if not (request.user.is_superuser or can_approve(request.user)):
            raise PermissionDenied(_("Permission denied"))
        return self.get_object()

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "pks": build_array_type(build_basic_type(OpenApiTypes.STR)),
                },
                required=["pks"],
                description="主键列表",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-approve")
    def batch_approve(self, request, *args, **kwargs):
        """批量通过审批单"""
        # 与单条 approve/reject 口径一致：批量入口同样先校验审批权限
        # （取值域过滤只能保证「看得到」，不能保证「有权审批」）
        if not (request.user.is_superuser or can_approve(request.user)):
            raise PermissionDenied(_("Permission denied"))
        pks = request.data.get("pks") or []
        if not pks:
            raise ValidationError(_("Please select the data to operate"))
        succeeded, failed = 0, []
        for approval in self.filter_queryset(self.get_queryset()).filter(pk__in=pks):
            ok, detail = approve_request(approval, request.user)
            if ok:
                succeeded += 1
            else:
                failed.append(f"{str(approval.pk)[:8].upper()}: {detail}")
        return ApiResponse(
            data={"succeeded": succeeded, "failed": failed},
            detail=_("Operation successful. Approved {} data").format(succeeded),
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True)
    def approve(self, request, *args, **kwargs):
        """通过审批单"""
        approval = self._get_actionable(request)
        ok, detail = approve_request(approval, request.user)
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        return ApiResponse(detail=_("The approval request has been approved"))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"reason": build_basic_type(OpenApiTypes.STR)},
                required=["reason"],
                description="驳回原因",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True)
    def reject(self, request, *args, **kwargs):
        """驳回审批单（必填原因）"""
        approval = self._get_actionable(request)
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError(_("Rejection reason is required"))
        ok, detail = reject_request(approval, request.user, reason)
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        return ApiResponse(detail=_("The approval request has been rejected"))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True)
    def cancel(self, request, *args, **kwargs):
        """撤回审批单（仅申请人、仅待审批）"""
        approval = self.get_object()
        ok, detail = cancel_request(approval, request.user)
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        return ApiResponse(detail=_("The approval request has been cancelled"))
