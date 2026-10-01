#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批实例的批量动作端点（自 approval_instance_actions.py 拆分，URL 路径 / 权限点 / 行为不变）。

包含：批量通过 / 批量驳回 / 批量转交。ViewSet 经 ApprovalInstanceActionMixin 组合本
mixin 后对外行为与拆分前完全一致。
"""

from typing import TYPE_CHECKING, Any

from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError

from approval.models.approval import ApprovalInstance, ApprovalNodeTask
from approval.utils.approval_flow import approve_task, reject_task, transfer_task, visible_instances_for
from approval.utils.approval_mfa import ensure_approval_action_confirmed
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema


class ApprovalInstanceBatchMixin:
    """实例批量动作端点（self 由组合它的 ViewSet 提供：get_object / filter_queryset 等）。"""

    if TYPE_CHECKING:  # 宿主 ViewSet 提供的接口（mixin 模式）

        def get_object(self, *args, **kwargs) -> Any: ...

        def get_queryset(self, *args, **kwargs): ...

        def filter_queryset(self, queryset, *args, **kwargs): ...

    def _my_current_task(self, instance, user):
        """我的当前待办（无则 None）：批量转交逐条使用（与单条 _resolve_task 同口径，不抛错）"""
        if instance.status != ApprovalInstance.Status.PENDING or instance.current_node_id is None:
            return None
        return instance.tasks.filter(
            assignee=user, status=ApprovalNodeTask.Status.PENDING, node=instance.current_node
        ).first()

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "pks": build_array_type(build_basic_type(OpenApiTypes.STR) or {}),
                    "comment": build_basic_type(OpenApiTypes.STR),
                },
                required=["pks"],
                description="主键列表 + 审批意见",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-approve")
    def batch_approve(self, request, *args, **kwargs):
        """批量通过（逐个定位当前用户的待办任务，返回成功数与被拒明细）"""
        ensure_approval_action_confirmed(request, "batch_approve")
        pks = request.data.get("pks") or []
        if not pks:
            raise ValidationError(_("Please select the data to operate"))
        comment = (request.data.get("comment") or "").strip()
        succeeded, failed = 0, []
        queryset = self.filter_queryset(self.get_queryset()).filter(pk__in=pks)
        for instance in queryset:
            task = instance.tasks.filter(
                assignee=request.user, status=ApprovalNodeTask.Status.PENDING, node=instance.current_node
            ).first()
            if task is None:
                failed.append({"no": str(instance.pk)[:8].upper(), "reason": str(_("No pending task for you"))})
                continue
            ok, detail = approve_task(task.pk, request.user, comment)
            if ok:
                succeeded += 1
            else:
                failed.append({"no": str(instance.pk)[:8].upper(), "reason": str(detail)})
        return ApiResponse(
            data={"succeeded": succeeded, "failed": failed},
            detail=_("Operation successful. Approved {} data").format(succeeded),
        )

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "pks": build_array_type(build_basic_type(OpenApiTypes.STR) or {}),
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
        """批量驳回（原因必填）"""
        ensure_approval_action_confirmed(request, "batch_reject")
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError(_("Rejection reason is required"))
        pks = request.data.get("pks") or []
        if not pks:
            raise ValidationError(_("Please select the data to operate"))
        succeeded, failed = 0, []
        queryset = self.filter_queryset(self.get_queryset()).filter(pk__in=pks)
        for instance in queryset:
            task = instance.tasks.filter(
                assignee=request.user, status=ApprovalNodeTask.Status.PENDING, node=instance.current_node
            ).first()
            if task is None:
                failed.append({"no": str(instance.pk)[:8].upper(), "reason": str(_("No pending task for you"))})
                continue
            ok, detail = reject_task(task.pk, request.user, reason)
            if ok:
                succeeded += 1
            else:
                failed.append({"no": str(instance.pk)[:8].upper(), "reason": str(detail)})
        return ApiResponse(
            data={"succeeded": succeeded, "failed": failed},
            detail=_("Operation successful. Rejected {} data").format(succeeded),
        )

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "pks": build_array_type(build_basic_type(OpenApiTypes.STR) or {}),
                    "username": build_basic_type(OpenApiTypes.STR),
                    "comment": build_basic_type(OpenApiTypes.STR),
                },
                required=["pks", "username"],
                description="实例主键列表 + 转交目标用户名 + 说明（逐条独立校验）",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-transfer")
    def batch_transfer(self, request, *args, **kwargs):
        """批量转交：把我的多条待办一次性转给同一用户（逐条独立，返回成功数 + 失败明细）。

        与单条同口径：仅「我的当前待办」可转；无待办的实例计入失败明细而非整体拒绝
        （勾选里混入他人任务/已流转实例时，仍完成可转的部分）。
        """
        ensure_approval_action_confirmed(request, "transfer")
        pks = request.data.get("pks") or []
        if not isinstance(pks, (list, tuple)) or not pks:
            return ApiResponse(code=1001, detail=_("Please select the applications to transfer"))
        username = (request.data.get("username") or "").strip()
        if not username:
            return ApiResponse(code=1001, detail=_("Please select the user to transfer to"))
        comment = (request.data.get("comment") or "").strip()

        # 取值域收敛：只能对自己可见的实例操作（越权 pk 直接计失败，不泄露存在性）
        visible = visible_instances_for(request.user).filter(pk__in=pks)
        instance_map = {str(instance.pk): instance for instance in visible}
        success, failures = 0, []
        for pk in pks:
            instance = instance_map.get(str(pk))
            if instance is None:
                failures.append({"pk": str(pk), "detail": str(_("No visible application for the given id"))})
                continue
            task = self._my_current_task(instance, request.user)
            if task is None:
                failures.append({"pk": str(pk), "detail": str(_("No pending task available for the current user"))})
                continue
            ok, detail = transfer_task(task.pk, request.user, username, comment)
            if ok:
                success += 1
            else:
                failures.append({"pk": str(pk), "detail": detail})
        if not success:
            # 全失败：以整体失败返回（前端按普通业务失败提示，明细随 data 带回）
            detail = failures[0]["detail"] if failures else str(_("The approval task has been transferred"))
            return ApiResponse(code=1001, detail=detail, data={"success": 0, "failures": failures})
        return ApiResponse(
            data={"success": success, "failures": failures},
            detail=_("{success} transferred, {failed} failed").format(success=success, failed=len(failures)),
        )
