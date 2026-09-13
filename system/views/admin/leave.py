#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""请假申请（ADR-032）：审批流引擎的第一个真实业务接入方。

- 新增即提交：创建成功立刻绑定「请假审批流程」发起实例（无可用流程时保留草稿并提示）；
- 撤回：仅申请人、仅审批中；通过/驳回在「流程审批」中心处理，业务单状态由引擎
  终态信号自动回写（本视图不含审批动作，避免出现第二套审批入口）；
- 取值域：超管全部；普通用户「我提交 ∪ 我审批过（待办/已办）」。
"""

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.filters import BaseFilterBackend, OrderingFilter

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models.approval import ApprovalNodeTask
from system.models.leave import Leave
from system.serializers.leave import LeaveSerializer
from system.utils.leave import cancel_leave, leave_conflict_queryset, leave_stats, submit_leave


class LeaveFilter(BaseFilterSet):
    reason = filters.CharFilter(field_name="reason", lookup_expr="icontains")

    class Meta:
        model = Leave
        fields = ["leave_type", "status", "creator", "start_date", "end_date", "created_time", "reason"]


class LeaveScopeFilter(BaseFilterBackend):
    """页签取值域：scope=mine（我提交）/ todo（待我审批）/ done（我已办）。

    缺省 = 可见域（我提交 ∪ 我审批过）；审批中的单同时出现在申请人与审批人视图，
    属预期（与流程审批中心的页签口径一致）。
    """

    def filter_queryset(self, request, queryset, view):
        user = request.user
        if not user or not user.is_authenticated:
            return queryset.none()
        scope = request.query_params.get("scope")
        if scope == "mine":
            return queryset.filter(creator=user)
        if scope == "todo":
            return queryset.filter(
                instance__tasks__status=ApprovalNodeTask.Status.PENDING, instance__tasks__assignee=user
            ).distinct()
        if scope == "done":
            return queryset.filter(instance__tasks__actor=user).distinct()
        return leave_conflict_queryset(queryset, user)


class LeaveViewSet(BaseModelSet):
    """请假申请"""

    queryset = Leave.objects.select_related("creator", "instance", "instance__current_node")
    serializer_class = LeaveSerializer
    filterset_class = LeaveFilter
    filter_backends = (DjangoFilterBackend, OrderingFilter, LeaveScopeFilter)
    ordering = ["-created_time"]
    ordering_fields = ["created_time", "start_date", "end_date", "days"]

    def get_queryset(self):
        return leave_conflict_queryset(super().get_queryset(), self.request.user)

    def perform_create(self, serializer):
        user = self.request.user
        serializer.save(creator=user, modifier=user, dept_belong=getattr(user, "dept", None))

    def create(self, request, *args, **kwargs):
        """新增请假申请（保存后立即提交审批；无可用流程时保留草稿并提示）"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        leave = serializer.instance
        ok, error = submit_leave(leave, request.user)
        leave.refresh_from_db()
        data = self.get_serializer(leave).data
        if not ok:
            return ApiResponse(data=data, detail=_("Saved as draft: {}").format(error))
        return ApiResponse(data=data, detail=_("The leave request has been submitted for approval"))

    def perform_destroy(self, instance):
        if instance.status in (Leave.Status.PENDING, Leave.Status.APPROVED):
            raise ValidationError({"detail": _("Requests in approval or already approved cannot be deleted")})
        return super().perform_destroy(instance)

    def batch_destroy(self, request, *args, **kwargs):
        """批量删除：静默排除审批中/已批准的申请，不因单条受保护而整批失败。"""
        self.queryset = self.queryset.exclude(status__in=[Leave.Status.PENDING, Leave.Status.APPROVED])
        return super().batch_destroy(request, *args, **kwargs)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True)
    def submit(self, request, *args, **kwargs):
        """提交审批（草稿或已驳回/已撤回的申请可重新提交）"""
        leave = self.get_object()
        ok, detail = submit_leave(leave, request.user)
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        return ApiResponse(detail=_("The leave request has been submitted for approval"))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True)
    def cancel(self, request, *args, **kwargs):
        """撤回申请（仅申请人、仅审批中）"""
        leave = self.get_object()
        ok, detail = cancel_leave(leave, request.user)
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        return ApiResponse(detail=_("The leave request has been cancelled"))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False)
    def stats(self, request, *args, **kwargs):
        """我的请假统计（近 30 天：提交 / 审批中 / 已通过 / 已驳回）"""
        return ApiResponse(data=leave_stats(request.user))
