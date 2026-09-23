#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批实例的动作端点（自 approval_flow.py 拆出，仅因行数门禁；URL 路径 / 权限点 / 行为不变）。

包含：通过 / 驳回 / 加签 / 转交（含批量）/ 催办 / 撤回 / 批量通过驳回 / 待办计数 /
统计 / 可发起流程。ViewSet 组合本 mixin 后对外行为与拆分前完全一致。
"""

import re

from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError

from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models.approval import ApprovalFlow, ApprovalInstance, ApprovalInstanceComment, ApprovalNodeTask
from system.serializers.approval_instance import ApprovalInstanceCommentSerializer
from system.utils.approval.display import user_display
from system.utils.approval_flow import (
    FLOW_STATS_WINDOW_DAYS,
    add_sign,
    approve_task,
    cancel_instance,
    instance_stats,
    node_progress_for,
    pending_count_for,
    reject_task,
    transfer_task,
    urge_instance,
    visible_instances_for,
)
from system.utils.approval_mfa import ensure_approval_action_confirmed

# 评论 @ 提及：与聊天室提及同口径（用户名，允许 . - _）
MENTION_PATTERN = re.compile(r"@([\w.\-]+)")
COMMENT_MAX_LENGTH = 2000


class ApprovalInstanceActionMixin:
    """实例动作端点（self 由组合它的 ViewSet 提供：get_object / filter_queryset 等）。"""

    def _resolve_comment_mentions(self, content, exclude_user):
        """解析评论中的 @用户名 → 启用用户列表（排除自己，去重保序）。"""
        from system.models import UserInfo

        names = []
        for name in MENTION_PATTERN.findall(content or ""):
            if name and name not in names:
                names.append(name)
        if not names:
            return []
        users = []
        for user in UserInfo.objects.filter(username__in=names[:20], is_active=True):
            if user.pk != exclude_user.pk:
                users.append(user)
        return users

    def _notify_comment_mentions(self, instance, users, content):
        """评论 @ 提醒（默认只提醒被提及者；失败只记日志）。"""
        if not users:
            return
        from system.notifications import ApprovalFlowMessage

        for user in users:
            try:
                ApprovalFlowMessage(user, "mentioned", instance, extra=(content or "")[:120]).publish(is_async=True)
            except Exception:  # noqa: BLE001 通知失败不影响评论
                pass

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

    def _my_current_task(self, instance, user):
        """我的当前待办（无则 None）：批量转交逐条使用（与单条 _resolve_task 同口径，不抛错）"""
        if instance.status != ApprovalInstance.Status.PENDING or instance.current_node_id is None:
            return None
        return instance.tasks.filter(
            assignee=user, status=ApprovalNodeTask.Status.PENDING, node=instance.current_node
        ).first()

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="comments")
    def comments(self, request, *args, **kwargs):
        """讨论区评论列表"""
        instance = self.get_object()
        rows = instance.comments.select_related("creator").all()
        return ApiResponse(data=ApprovalInstanceCommentSerializer(rows, many=True).data)

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"content": build_basic_type(OpenApiTypes.STR)},
                required=["content"],
                description="评论内容（支持 @用户名 提醒）",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True, url_path="comment")
    def add_comment(self, request, *args, **kwargs):
        """发表评论（参与人 = 可见域内用户；@ 提及者收提醒）"""
        instance = self.get_object()
        content = str(request.data.get("content") or "").strip()
        if not content:
            return ApiResponse(code=1004, detail=_("Comment content is required"))
        if len(content) > COMMENT_MAX_LENGTH:
            return ApiResponse(
                code=1004, detail=_("Comment is too long (max {} characters)").format(COMMENT_MAX_LENGTH)
            )
        mentioned = self._resolve_comment_mentions(content, request.user)
        comment = ApprovalInstanceComment.objects.create(
            instance=instance,
            content=content,
            creator=request.user,
            author_display=user_display(request.user),
            mentions=[str(user.pk) for user in mentioned],
        )
        self._notify_comment_mentions(instance, mentioned, content)
        return ApiResponse(data=ApprovalInstanceCommentSerializer(comment).data, detail=_("Comment published"))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"pk": build_basic_type(OpenApiTypes.STR)},
                required=["pk"],
                description="评论主键",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True, url_path="comment/delete")
    def delete_comment(self, request, *args, **kwargs):
        """删除评论（作者本人或超管）"""
        instance = self.get_object()
        comment = instance.comments.filter(pk=request.data.get("pk")).first()
        if comment is None:
            return ApiResponse(code=1004, detail=_("Comment does not exist"))
        if comment.creator_id != request.user.pk and not request.user.is_superuser:
            raise PermissionDenied(_("You can only delete your own comments"))
        comment.delete()
        return ApiResponse(detail=_("Deleted successfully"))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "pks": build_array_type(build_basic_type(OpenApiTypes.STR)),
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

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="available-flows")
    def available_flows(self, request, *args, **kwargs):
        """可发起流程（启用中）：发起申请弹窗的数据源（无需流程定义管理权限）。

        普通申请人通常没有「流程定义」页权限，因此单独开一个轻量只读入口，
        只回传发起所需的 pk/name/form_schema，不暴露节点审批人配置。
        """
        flows = ApprovalFlow.objects.filter(is_active=True).order_by("-created_time")
        return ApiResponse(
            data=[
                {
                    "pk": str(flow.pk),
                    "name": flow.name,
                    "code": flow.code,
                    "form_schema": flow.form_schema or [],
                }
                for flow in flows
            ]
        )

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

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "pks": build_array_type(build_basic_type(OpenApiTypes.STR)),
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
