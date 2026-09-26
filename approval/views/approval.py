#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批中心：审批单查询 / 通过 / 驳回 / 撤回 / 批量通过 / 批量驳回 / 待办计数 / 统计。

取值域不走通用数据权限：超管可见全部，普通用户可见「我发起的 + 待我审批
（PENDING，不含本人发起）+ 我审批过的」，与待办口径一致。
"""

from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.filters import BaseFilterBackend, OrderingFilter
from rest_framework.viewsets import GenericViewSet

from approval.models.approval import ApprovalRequest
from approval.serializers.approval import ApprovalRequestDetailSerializer, ApprovalRequestSerializer
from approval.utils.approval import (
    approval_stats,
    approve_request,
    can_act,
    can_approve,
    cancel_request,
    pending_count_for,
    pending_queryset_for,
    reject_request,
)
from approval.utils.approval_mfa import ensure_approval_action_confirmed
from common.core.filter import BaseFilterSet
from common.core.modelset import BaseViewSet, DetailAction, ListAction, SearchColumnsAction, SearchFieldsAction
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger

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
            # 待办口径与 pending_count_for 共用同一函数（角标数与页签行数必须一致）：
            # 扁平单给全局审批人，多级链单只给当前级候选人，本人发起恒排除
            return pending_queryset_for(user)
        if scope == "mine":
            return queryset.filter(creator=user)
        if user.is_superuser:
            return queryset
        # 缺省页签：我发起的 ∪ 我审批过的 ∪ 我当前可审的（含多级链当前级候选人）
        mine = Q(creator=user) | Q(approver=user) | Q(current_level__gt=0, current_assignees=user)
        if can_approve(user):
            mine |= Q(status=ApprovalRequest.Status.PENDING, current_level=0)
        return queryset.filter(mine).distinct()


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
    # current_assignees（当前级候选人投影）列表逐行展示：prefetch 避免 N+1
    prefetch_related_fields = ("current_assignees",)

    def get_serializer_class(self):
        # 详情额外返回 steps（多级审批链进度），列表保持轻量
        if self.action == "retrieve":
            return ApprovalRequestDetailSerializer
        return ApprovalRequestSerializer

    def _get_actionable(self, request):
        """取审批单并校验审批权限：多级链 = 当前级候选人；扁平单 = 超管或审批人集合。

        申请人不能自审在引擎动作内判断（保持与批量入口同一收口点）。
        """
        approval = self.get_object()
        if (approval.current_level or 0) > 0:
            if not can_act(approval, request.user):
                raise PermissionDenied(_("Permission denied"))
            return approval
        if not (request.user.is_superuser or can_approve(request.user)):
            raise PermissionDenied(_("Permission denied"))
        return approval

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
        ensure_approval_action_confirmed(request, "batch_approve")
        # 授权收口：全局审批人（超管 / 角色或权限反查）直接放行；多级链候选人
        # 至少要是某张在途单的当前级候选（更细的逐单校验在引擎内完成，
        # 无权处理的单进入 failed 明细，而不是整体 403）
        if not (request.user.is_superuser or can_approve(request.user)):
            if not pending_queryset_for(request.user).exists():
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

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "pks": build_array_type(build_basic_type(OpenApiTypes.STR)),
                    "reason": build_basic_type(OpenApiTypes.STR),
                },
                required=["pks", "reason"],
                description="主键列表 + 驳回原因",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-reject")
    def batch_reject(self, request, *args, **kwargs):
        """批量驳回审批单（原因必填；逐单校验状态与审批人，返回成功数与被拒明细）"""
        ensure_approval_action_confirmed(request, "batch_reject")
        # 授权收口同 batch_approve：全局审批人或某张在途单的当前级候选人
        if not (request.user.is_superuser or can_approve(request.user)):
            if not pending_queryset_for(request.user).exists():
                raise PermissionDenied(_("Permission denied"))
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError(_("Rejection reason is required"))
        pks = request.data.get("pks") or []
        if not pks:
            raise ValidationError(_("Please select the data to operate"))
        succeeded, failed = 0, []
        for approval in self.filter_queryset(self.get_queryset()).filter(pk__in=pks):
            ok, detail = reject_request(approval, request.user, reason)
            if ok:
                succeeded += 1
            else:
                # 与 batch-approve 的「单号: 原因」等价的可读明细（前端逐条展示）
                failed.append({"no": str(approval.pk)[:8].upper(), "reason": str(detail)})
        return ApiResponse(
            data={"succeeded": succeeded, "failed": failed},
            detail=_("Operation successful. Rejected {} data").format(succeeded),
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="pending-count")
    def pending_count(self, request, *args, **kwargs):
        """待我审批数（轻量接口：供顶栏/页签角标轮询，服务端 10s 短缓存，非审批人返回 0）"""
        return ApiResponse(data={"pending": pending_count_for(request.user)})

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False)
    def stats(self, request, *args, **kwargs):
        """审批统计（近 30 天：我提交 / 我通过 / 我驳回 / 平均审批时长 / 我的待办）"""
        return ApiResponse(data=approval_stats(request.user))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True)
    def approve(self, request, *args, **kwargs):
        """通过审批单（可选 comment：多级链逐级留痕）"""
        ensure_approval_action_confirmed(request, "approve")
        approval = self._get_actionable(request)
        comment = (request.data.get("comment") or "").strip()
        ok, detail = approve_request(approval, request.user, comment)
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        # 会签未齐时引擎返回「已记录，等待其他会签人 (n/m)」的 detail，原样透传
        return ApiResponse(detail=detail or _("The approval request has been approved"))

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
        ensure_approval_action_confirmed(request, "reject")
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
        ensure_approval_action_confirmed(request, "cancel")
        approval = self.get_object()
        ok, detail = cancel_request(approval, request.user)
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        return ApiResponse(detail=_("The approval request has been cancelled"))
