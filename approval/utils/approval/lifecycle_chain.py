#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批：多级审批链的通过 / 驳回推进（自 lifecycle.py 拆分，行为不变）。

供 lifecycle.py 的 approve_request / reject_request 在 current_level > 0 时调用。
"""

from typing import Any

from django.utils.translation import gettext_lazy as _

from .approved_actions import run_on_approved
from .chains import can_act, current_step, sync_current_level
from .display import user_display
from .notify import _emit_approval_event, notify_applicant, notify_step
from .queries import invalidate_pending_count_cache


def _approve_chain(approval: Any, user: Any, comment: str) -> Any:
    """多级链通过：标记当前级 → 推进下一级；末级通过才落整单终态（供 approve_request 调用）。"""
    import datetime

    from django.db import transaction
    from django.utils import timezone

    from approval.models import ApprovalRequest, ApprovalRequestStep, ApprovalRequestStepAction
    from common.core.config import SysConfig

    if approval.status != ApprovalRequest.Status.PENDING:
        return False, _("Only pending requests can be approved")
    if approval.creator_id == user.pk:
        return False, _("The applicant cannot approve their own request")
    if not can_act(approval, user):
        # 会签已通过者会落在这里（can_act 对已处理者返回 False）：
        # 前端按钮已隐藏，此处兜住并发/重放，给出精准提示
        level = current_step(approval)
        if (
            level is not None
            and level.approve_type == ApprovalRequestStep.ApproveType.AND
            and level.actions.filter(approver=user).exists()
        ):
            return False, _("You have already handled the current level")
        return False, _("You are not the approver of the current level")

    now = timezone.now()
    with transaction.atomic():
        step = approval.steps.filter(order=approval.current_level, status=ApprovalRequestStep.Status.PENDING).first()
        if step is None:
            return False, _("The current approval level is no longer pending")
        # 逐人动作留痕（或签/会签一致）：同一人重复提交被唯一约束挡住
        _action, created = ApprovalRequestStepAction.objects.get_or_create(
            step=step,
            approver=user,
            defaults={
                "status": ApprovalRequestStepAction.Status.APPROVED,
                "comment": (comment or "")[:255] or None,
                "approver_display": user_display(user),
            },
        )
        if not created:
            return False, _("You have already handled the current level")
        if step.approve_type == ApprovalRequestStep.ApproveType.AND:
            # 会签：全员通过才推进；未齐时该级保持 PENDING（当前级不变，等待其余候选人）
            approved_count = step.actions.filter(status=ApprovalRequestStepAction.Status.APPROVED).count()
            required = step.assignees.count()
            if approved_count < required:
                ApprovalRequestStep.objects.filter(pk=step.pk).update(
                    approver=user,
                    approver_display=user_display(user),
                    comment=(comment or "")[:255] or None,
                    acted_at=now,
                    updated_time=now,
                )
                return True, _("Your approval has been recorded, waiting for other approvers ({done}/{total})").format(
                    done=approved_count, total=required
                )
        updated = ApprovalRequestStep.objects.filter(pk=step.pk, status=ApprovalRequestStep.Status.PENDING).update(
            status=ApprovalRequestStep.Status.APPROVED,
            approver=user,
            approver_display=user_display(user),
            comment=(comment or "")[:255] or None,
            acted_at=now,
            updated_time=now,
        )
        if not updated:
            # 并发落败：同一级已被他人处理，回读真实状态供调用方展示
            approval.refresh_from_db(fields=["status", "current_level"])
            return False, _("Only pending requests can be approved")
        nxt = (
            approval.steps.filter(status=ApprovalRequestStep.Status.PENDING)
            .filter(order__gt=step.order)
            .order_by("order")
            .first()
        )
        if nxt is not None:
            # 中间级：整单保持 PENDING，仅切换当前级并通知下一级候选人
            sync_current_level(approval, nxt)
            notify_step(approval, nxt)
            invalidate_pending_count_cache()
            return True, None
        # 末级通过：整单终态 + 令牌有效期（申请人可在有效期内重发原请求）
        ApprovalRequest.objects.filter(pk=approval.pk, status=ApprovalRequest.Status.PENDING).update(
            status=ApprovalRequest.Status.APPROVED,
            approver=user,
            approver_display=user_display(user),
            approved_at=now,
            expired_at=now + datetime.timedelta(seconds=int(SysConfig.APPROVAL_TOKEN_TTL)),
            updated_time=now,
        )
        sync_current_level(approval, None)

    approval.status = ApprovalRequest.Status.APPROVED
    approval.approver = user
    approval.approved_at = now
    approval.expired_at = now + datetime.timedelta(seconds=int(SysConfig.APPROVAL_TOKEN_TTL))
    invalidate_pending_count_cache()
    notify_applicant(approval, "approved")
    _emit_approval_event("approval.approved", approval)
    # 自动完成：登记了通过后动作的路径在此落库，申请人无需再手动重放
    run_on_approved(approval, user)
    return True, None


def _reject_chain(approval: Any, user: Any, reason: str) -> Any:
    """多级链驳回：当前级置 REJECTED、其余在途级作废、整单终止（供 reject_request 调用）。"""
    from django.db import transaction
    from django.utils import timezone

    from approval.models import ApprovalRequest, ApprovalRequestStep, ApprovalRequestStepAction

    if approval.status != ApprovalRequest.Status.PENDING:
        return False, _("Only pending requests can be rejected")
    if approval.creator_id == user.pk:
        return False, _("The applicant cannot approve their own request")
    if not can_act(approval, user):
        return False, _("You are not the approver of the current level")

    now = timezone.now()
    with transaction.atomic():
        step = approval.steps.filter(order=approval.current_level, status=ApprovalRequestStep.Status.PENDING).first()
        if step is not None:
            # 驳回留痕（会签场景：任一人驳回即整单终止，动作记录一并保留）
            ApprovalRequestStepAction.objects.get_or_create(
                step=step,
                approver=user,
                defaults={
                    "status": ApprovalRequestStepAction.Status.REJECTED,
                    "comment": (reason or "")[:255] or None,
                    "approver_display": user_display(user),
                },
            )
        updated = ApprovalRequestStep.objects.filter(
            request=approval, order=approval.current_level, status=ApprovalRequestStep.Status.PENDING
        ).update(
            status=ApprovalRequestStep.Status.REJECTED,
            approver=user,
            approver_display=user_display(user),
            comment=(reason or "")[:255] or None,
            acted_at=now,
            updated_time=now,
        )
        if not updated:
            approval.refresh_from_db(fields=["status", "current_level"])
            return False, _("Only pending requests can be rejected")
        # 其余在途级统一作废（驳回即终止，一期不做「回退上一节点」）
        ApprovalRequestStep.objects.filter(request=approval, status=ApprovalRequestStep.Status.PENDING).update(
            status=ApprovalRequestStep.Status.CANCELLED, updated_time=now
        )
        ApprovalRequest.objects.filter(pk=approval.pk, status=ApprovalRequest.Status.PENDING).update(
            status=ApprovalRequest.Status.REJECTED,
            approver=user,
            approver_display=user_display(user),
            approved_at=now,
            reason=(reason or "")[:255],
            updated_time=now,
        )
        sync_current_level(approval, None)

    approval.status = ApprovalRequest.Status.REJECTED
    approval.approver = user
    approval.approved_at = now
    approval.reason = (reason or "")[:255]
    invalidate_pending_count_cache()
    notify_applicant(approval, "rejected")
    _emit_approval_event("approval.rejected", approval)
    return True, None
