#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批实例的动作端点（自 approval_flow.py 拆出，仅因行数门禁；URL 路径 / 权限点 / 行为不变）。

包含：通过 / 驳回 / 加签 / 转交（含批量）/ 催办 / 撤回 / 批量通过驳回 / 待办计数 /
统计 / 可发起流程。ViewSet 组合本 mixin 后对外行为与拆分前完全一致。
"""

from typing import TYPE_CHECKING, Any

from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError

from approval.models.approval import ApprovalFlow, ApprovalNodeTask
from approval.utils.approval_flow import (
    FLOW_STATS_WINDOW_DAYS,
    add_sign,
    approve_task,
    cancel_instance,
    instance_stats,
    node_progress_for,
    pending_count_for,
    reject_task,
    return_instance,
    returnable_nodes,
    transfer_task,
    urge_instance,
)
from approval.utils.approval_flow import (
    remove_sign as remove_sign_task,
)
from approval.utils.approval_mfa import ensure_approval_action_confirmed
from approval.views.approval_instance_batch import ApprovalInstanceBatchMixin
from approval.views.approval_instance_comments import ApprovalInstanceCommentMixin
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils.datasource import limit_datasource, truncation_detail


class ApprovalInstanceActionMixin(ApprovalInstanceBatchMixin, ApprovalInstanceCommentMixin):
    """实例动作端点（self 由组合它的 ViewSet 提供：get_object / filter_queryset 等）。"""

    if TYPE_CHECKING:  # 宿主 ViewSet 提供的接口（mixin 模式）

        def get_object(self, *args, **kwargs) -> Any: ...

    def _resolve_task(self, instance, request):
        """定位要处理的任务：优先请求体 task，缺省取「我的当前待办」（便于前端一键处理）。"""
        task_pk = request.data.get("task")
        queryset = instance.tasks.all()
        if task_pk:
            queryset = queryset.filter(pk=task_pk)
        else:
            queryset = queryset.filter(
                assignee=request.user, status=ApprovalNodeTask.Status.PENDING, node=instance.current_node
            )
        task = queryset.first()
        if task is None:
            raise ValidationError({"detail": _("No pending task available for the current user")})
        return task

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="available-flows")
    def available_flows(self, request, *args, **kwargs):
        """可发起流程（启用中）：发起申请弹窗的数据源（无需流程定义管理权限）。

        普通申请人通常没有「流程定义」页权限，因此单独开一个轻量只读入口，
        只回传发起所需的 pk/name/form_schema，不暴露节点审批人配置。
        小集合接口：超过量级上限时截断并随响应给出提示（见 limit_datasource）。
        """
        flows, truncated = limit_datasource(
            ApprovalFlow.objects.filter(is_active=True).order_by("-created_time"), name="available-flows"
        )
        data = [
            {
                "pk": str(flow.pk),
                "name": flow.name,
                "code": flow.code,
                "form_schema": flow.form_schema or [],
            }
            for flow in flows
        ]
        if truncated:
            return ApiResponse(data=data, detail=truncation_detail())
        return ApiResponse(data=data)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="pending-count")
    def pending_count(self, request, *args, **kwargs):
        """待我审批数（轻量接口：供顶栏/页签角标轮询，服务端 10s 短缓存）"""
        return ApiResponse(data={"pending": pending_count_for(request.user)})

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False)
    def stats(self, request, *args, **kwargs):
        """流程审批统计（近 30 天：我提交 / 我通过 / 我驳回 / 我的待办）"""
        return ApiResponse(data=instance_stats(request.user, days=FLOW_STATS_WINDOW_DAYS))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "task": build_basic_type(OpenApiTypes.STR),
                    "comment": build_basic_type(OpenApiTypes.STR),
                },
                description="任务主键（缺省取我的当前待办）+ 审批意见",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True)
    def approve(self, request, *args, **kwargs):
        """通过（或签任一通过 / 会签全部通过后流转下一节点）"""
        ensure_approval_action_confirmed(request, "approve")
        instance = self.get_object()
        task = self._resolve_task(instance, request)
        ok, detail = approve_task(task.pk, request.user, (request.data.get("comment") or "").strip())
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        return ApiResponse(detail=_("The approval task has been approved"))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "task": build_basic_type(OpenApiTypes.STR),
                    "reason": build_basic_type(OpenApiTypes.STR),
                },
                required=["reason"],
                description="任务主键（缺省取我的当前待办）+ 驳回原因",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True)
    def reject(self, request, *args, **kwargs):
        """驳回（原因必填；驳回即终止申请）"""
        ensure_approval_action_confirmed(request, "reject")
        instance = self.get_object()
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError(_("Rejection reason is required"))
        task = self._resolve_task(instance, request)
        ok, detail = reject_task(task.pk, request.user, reason)
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        return ApiResponse(detail=_("The approval task has been rejected"))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"message": build_basic_type(OpenApiTypes.STR)},
                description="可选催办留言（随通知带到审批人）",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True)
    def urge(self, request, *args, **kwargs):
        """催办（仅申请人/超管、仅审批中）：通知当前节点审批人，10 分钟节流"""
        instance = self.get_object()
        ok, detail = urge_instance(instance, request.user, (request.data.get("message") or "").strip())
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        return ApiResponse(detail=_("The approval reminder has been sent"))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True)
    def cancel(self, request, *args, **kwargs):
        """撤回申请（仅申请人、仅审批中）"""
        ensure_approval_action_confirmed(request, "cancel")
        instance = self.get_object()
        ok, detail = cancel_instance(instance, request.user)
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        return ApiResponse(detail=_("The application has been cancelled"))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "usernames": build_basic_type(OpenApiTypes.STR),
                    "comment": build_basic_type(OpenApiTypes.STR),
                },
                required=["usernames"],
                description="加签审批人用户名（逗号分隔）+ 说明",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True, url_path="add-sign")
    def add_sign_action(self, request, *args, **kwargs):
        """加签：在当前节点追加审批人（当前节点参与人或超管可操作）"""
        ensure_approval_action_confirmed(request, "add_sign")
        instance = self.get_object()
        if not (
            request.user.is_superuser
            or instance.tasks.filter(Q(assignee=request.user) | Q(actor=request.user)).exists()
        ):
            raise PermissionDenied(_("Permission denied"))
        ok, detail = add_sign(
            instance, request.user, request.data.get("usernames"), (request.data.get("comment") or "").strip()
        )
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        # 加签会抬高节点任务总数 → 返回新达标线（前端提示「加签后需 N 人通过」）
        instance.refresh_from_db()
        return ApiResponse(data={"node_progress": node_progress_for(instance)}, detail=_("The approver has been added"))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "task": build_basic_type(OpenApiTypes.STR),
                    "comment": build_basic_type(OpenApiTypes.STR),
                },
                required=["task"],
                description="要移除的加签任务主键 + 说明",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True, url_path="remove-sign")
    def remove_sign(self, request, *args, **kwargs):
        """减签：移除加签追加的候选（仅 is_added 的 PENDING 任务；或签节点拒绝）"""
        ensure_approval_action_confirmed(request, "remove_sign")
        instance = self.get_object()
        if not (
            request.user.is_superuser
            or instance.tasks.filter(Q(assignee=request.user) | Q(actor=request.user)).exists()
        ):
            raise PermissionDenied(_("Permission denied"))
        ok, detail = remove_sign_task(
            instance, request.user, request.data.get("task"), (request.data.get("comment") or "").strip()
        )
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        # 减签会降低节点任务总数 → 返回新达标线（与加签同口径的进度提示）
        instance.refresh_from_db()
        return ApiResponse(
            data={"node_progress": node_progress_for(instance)}, detail=_("The added approver has been removed")
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="return-targets")
    def return_targets(self, request, *args, **kwargs):
        """可退回节点（已途经、非当前，按 order 降序）：退回弹窗数据源"""
        instance = self.get_object()
        return ApiResponse(data=returnable_nodes(instance))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "task": build_basic_type(OpenApiTypes.STR),
                    "reason": build_basic_type(OpenApiTypes.STR),
                    "target_order": build_basic_type(OpenApiTypes.INT),
                },
                required=["reason"],
                description="退回原因（必填）+ 目标节点 order（缺省 = 上一途经节点，须已途经）",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True, url_path="return")
    def return_node(self, request, *args, **kwargs):
        """退回：当前节点待办作废，实例回退到已途经节点重新审批（处理人或超管）"""
        ensure_approval_action_confirmed(request, "return")
        instance = self.get_object()
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError(_("Return reason is required"))
        ok, detail = return_instance(
            instance,
            request.user,
            reason,
            target_order=request.data.get("target_order"),
            task_pk=request.data.get("task"),
        )
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        instance.refresh_from_db()
        return ApiResponse(
            data={"current_node": getattr(instance.current_node, "name", "")},
            detail=_("The application has been returned"),
        )

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "task": build_basic_type(OpenApiTypes.STR),
                    "username": build_basic_type(OpenApiTypes.STR),
                    "comment": build_basic_type(OpenApiTypes.STR),
                },
                required=["username"],
                description="任务主键（缺省取我的当前待办）+ 转交目标用户名 + 说明",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True)
    def transfer(self, request, *args, **kwargs):
        """转交：把当前待办转给指定用户处理（处理人本人或超管）"""
        ensure_approval_action_confirmed(request, "transfer")
        instance = self.get_object()
        task = self._resolve_task(instance, request)
        ok, detail = transfer_task(
            task.pk, request.user, request.data.get("username"), (request.data.get("comment") or "").strip()
        )
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        return ApiResponse(detail=_("The approval task has been transferred"))
