#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批：建单 / 消费令牌 / 状态流转。"""

import uuid

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

from common.core.response import ApiResponse

from .approved_actions import run_on_approved, snapshot_payload
from .approvers import build_module, can_approve, find_active_pending, resolve_approvers
from .chains import build_steps, can_act, create_steps, current_step, resolve_rule, sync_current_level
from .constants import APPROVAL_HEADER, APPROVAL_QUERY_PARAM
from .display import user_display
from .notify import (
    _emit_approval_event,
    forbidden_response,
    notify_applicant,
    notify_approvers,
    notify_step,
    pending_response,
)
from .payload import canonical_params, get_request_object_pk, get_request_params, path_intercepted
from .queries import invalidate_pending_count_cache
from .snapshot import build_target_snapshot


def create_approval(view, request, module: str = ""):
    """建 PENDING 单并通知审批人；无可用审批人时直接报错（避免永久 PENDING）。

    审批人来源两条路（见 system.models.ApprovalRule）：
    - 命中审批规则：按规则级次落多级审批链快照，通知第 1 级候选人；
    - 未命中规则：既有全局审批人集合（角色/权限反查/超管）单级扁平审批。

    级次快照 fail-closed：任一级无可用人（或仅申请人）即拒绝建单——避免中途卡死。

    module：调用方可显式指定审批单归属模块名（缺省取视图 docstring 首行），
    供非标准 CRUD 入口（如 AI 动作执行端点）给出可读的审批单标题。
    """
    from approval.models.approval import ApprovalRequest

    rule = resolve_rule(request.path)
    steps = None
    approvers = None
    if rule is not None:
        steps, error = build_steps(rule, request.user)
        if error:
            raise ValidationError(error)
    else:
        approvers = resolve_approvers(request.user)
        if not approvers.exists():
            raise ValidationError(_("No available approver, please contact the administrator to configure approvers"))

    params = get_request_params(request)
    approval = ApprovalRequest.objects.create(
        module=(module or build_module(view))[:64],
        method=request.method,
        path=request.path,
        object_pk=get_request_object_pk(view),
        params=params,
        payload=snapshot_payload(request),
        # 目标对象轻量快照（变更前后事实对照；不可达时为空 dict 降级展示）
        target_snapshot=build_target_snapshot(view, request),
        creator=request.user,
    )
    invalidate_pending_count_cache()
    if steps:
        rows = create_steps(approval, steps)
        sync_current_level(approval, rows[0])
        notify_step(approval, rows[0])
    else:
        notify_approvers(approval, approvers)
    _emit_approval_event("approval.submitted", approval)
    return approval


def consume_approval(request, approval_id):
    """消费审批令牌：返回 None 表示放行（调用方继续执行业务），否则返回协议响应。"""
    from django.utils import timezone

    from approval.models.approval import ApprovalRequest

    # 令牌来自请求头，非法 UUID 若直接进 filter 会抛 Django ValidationError →
    # 未被 DRF 异常处理器覆盖而返回 500（同 online.py 强制下线的 uuid 解析守护）
    try:
        approval_pk = uuid.UUID(str(approval_id))
    except (AttributeError, TypeError, ValueError):
        return forbidden_response(_("Invalid approval token"))
    approval = ApprovalRequest.objects.filter(pk=approval_pk).first()
    if approval is None:
        return forbidden_response(_("Invalid approval token"))
    if approval.creator_id != request.user.pk:
        return forbidden_response(_("The approval token does not belong to the current user"))

    # 请求指纹一致性：防止「批 A、B」的令牌被拿去执行「C、D」
    fingerprint_ok = (
        approval.method == request.method
        and approval.path == request.path
        and canonical_params(approval.params) == canonical_params(get_request_params(request))
    )
    if not fingerprint_ok:
        approval.status = ApprovalRequest.Status.FAILED
        approval.reason = _("Resent request does not match the approved snapshot")
        approval.save(update_fields=["status", "reason", "updated_time"])
        return forbidden_response(_("The resent request does not match the approved content"))

    if approval.status == ApprovalRequest.Status.PENDING:
        # 审批仍在途：原样返回令牌（不重复建单、不重复通知）
        return pending_response(approval)

    if approval.status != ApprovalRequest.Status.APPROVED:
        detail = _("The approval has been {}").format(approval.get_status_display())
        if approval.status == ApprovalRequest.Status.REJECTED and approval.reason:
            detail = _("The approval was rejected: {}").format(approval.reason)
        return forbidden_response(detail)

    if approval.auto_completed:
        # 审批通过后已自动落库：原样返回成功语义，别让申请人误以为提交失败
        return ApiResponse(detail=_("The approved operation has been completed automatically"))
    if approval.consume_time is not None:
        return forbidden_response(_("The approval token has already been used"))
    if approval.expired_at and approval.expired_at < timezone.now():
        approval.status = ApprovalRequest.Status.EXPIRED
        approval.save(update_fields=["status", "updated_time"])
        return forbidden_response(_("The approval token has expired"))

    # 原子消费（一次性令牌）：条件更新 + rowcount 判定，防并发重发双消费。
    # 不用 select_for_update：sqlite（单测/E2E 库）不支持 SELECT ... FOR UPDATE，
    # 条件更新在全部后端语义一致
    now = timezone.now()
    consumed = ApprovalRequest.objects.filter(
        pk=approval.pk,
        status=ApprovalRequest.Status.APPROVED,
        consume_time__isnull=True,
    ).update(consume_time=now, updated_time=now)
    if not consumed:
        return forbidden_response(_("The approval token has already been used"))
    return None


def process_approval(view, request):
    """装饰器主入口：返回 None 放行业务，否则返回协议响应（412/403）。

    全局清单为空时整体休眠（渐进启用），已携令牌的重发请求在休眠期直接放行。
    """
    if getattr(request, "_approval_pre_authorized", False):
        # AI 动作链路预授权：动作层已在 action/execute 完成同一操作指纹的强制审批
        # （412 协议一次性令牌已消费），内部 dispatch 到业务端点时不再重复拦截，
        # 否则会建第二张审批单且该单永远无法从 AI 链路消费（双重审批死循环）。
        # 该标记只能由服务端内部构造的请求设置（AI 动作执行器），外部 HTTP 请求
        # 无法注入请求对象属性，不存在绕过面。
        return None
    if not path_intercepted(request.path):
        return None

    approval_id = request.headers.get(APPROVAL_HEADER) or request.query_params.get(APPROVAL_QUERY_PARAM)
    if approval_id:
        return consume_approval(request, approval_id)

    approval = find_active_pending(request.user, request.method, request.path, get_request_params(request))
    if approval is None:
        approval = create_approval(view, request)
    return pending_response(approval)


def approve_request(approval, user, comment: str = ""):
    """审批通过：多级链逐级推进（末级通过才置 APPROVED + 令牌有效期）。返回 (ok, detail)。

    多级链的授权口径 = 当前级候选人（配置到谁就谁审，超管不越级）；
    扁平单保持既有口径（超管或全局审批人集合）。

    状态流转以条件更新（CAS）落库：并发窗口内两个审批人各持同一 PENDING 快照时，
    先到者赢、后到者落空返回失败，不会覆盖先到者写下的终态（sqlite/MySQL 通用，
    不依赖 select_for_update）。
    """
    import datetime

    from django.utils import timezone

    from approval.models.approval import ApprovalRequest
    from common.core.config import SysConfig

    if (approval.current_level or 0) > 0:
        return _approve_chain(approval, user, comment)
    if approval.status != ApprovalRequest.Status.PENDING:
        return False, _("Only pending requests can be approved")
    if approval.creator_id == user.pk:
        return False, _("The applicant cannot approve their own request")
    if not (user.is_superuser or can_approve(user)):
        # 引擎层自校验：视图层已快速拒绝，此处兜住批量/内部调用等旁路
        return False, _("Permission denied")
    now = timezone.now()
    updated = ApprovalRequest.objects.filter(pk=approval.pk, status=ApprovalRequest.Status.PENDING).update(
        status=ApprovalRequest.Status.APPROVED,
        approver=user,
        approver_display=user_display(user),
        approved_at=now,
        expired_at=now + datetime.timedelta(seconds=int(SysConfig.APPROVAL_TOKEN_TTL)),
        updated_time=now,
    )
    if not updated:
        # 并发落败：内存快照已过期，回读真实状态供调用方/序列化展示
        approval.refresh_from_db(fields=["status"])
        return False, _("Only pending requests can be approved")
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


def _approve_chain(approval, user, comment: str):
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


def reject_request(approval, user, reason: str):
    """驳回：reason 必填；多级链中任一级驳回即整单终止（其余在途级作废）。返回 (ok, detail)。

    状态流转以 CAS 落库（同 approve_request）。
    """
    from django.utils import timezone

    from approval.models.approval import ApprovalRequest

    if (approval.current_level or 0) > 0:
        return _reject_chain(approval, user, reason)
    if approval.status != ApprovalRequest.Status.PENDING:
        return False, _("Only pending requests can be rejected")
    if approval.creator_id == user.pk:
        return False, _("The applicant cannot approve their own request")
    if not (user.is_superuser or can_approve(user)):
        # 引擎层自校验：视图层已快速拒绝，此处兜住批量/内部调用等旁路
        return False, _("Permission denied")
    now = timezone.now()
    updated = ApprovalRequest.objects.filter(pk=approval.pk, status=ApprovalRequest.Status.PENDING).update(
        status=ApprovalRequest.Status.REJECTED,
        approver=user,
        approver_display=user_display(user),
        approved_at=now,
        reason=(reason or "")[:255],
        updated_time=now,
    )
    if not updated:
        approval.refresh_from_db(fields=["status"])
        return False, _("Only pending requests can be rejected")
    approval.status = ApprovalRequest.Status.REJECTED
    approval.approver = user
    approval.approved_at = now
    approval.reason = (reason or "")[:255]
    invalidate_pending_count_cache()
    notify_applicant(approval, "rejected")
    _emit_approval_event("approval.rejected", approval)
    return True, None


def _reject_chain(approval, user, reason: str):
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


def cancel_request(approval, user):
    """申请人撤回：仅本人、仅 PENDING。返回 (ok, detail)。状态流转以 CAS 落库。

    多级链：撤回时把在途级次统一置 CANCELLED（扁平单无级次，更新命中 0 行无副作用）。
    """
    from django.db import transaction
    from django.utils import timezone

    from approval.models import ApprovalRequest, ApprovalRequestStep

    if approval.creator_id != user.pk:
        return False, _("Only the applicant can cancel the request")
    if approval.status != ApprovalRequest.Status.PENDING:
        return False, _("Only pending requests can be cancelled")
    now = timezone.now()
    with transaction.atomic():
        updated = ApprovalRequest.objects.filter(pk=approval.pk, status=ApprovalRequest.Status.PENDING).update(
            status=ApprovalRequest.Status.CANCELLED, updated_time=now
        )
        if not updated:
            approval.refresh_from_db(fields=["status"])
            return False, _("Only pending requests can be cancelled")
        ApprovalRequestStep.objects.filter(request=approval, status=ApprovalRequestStep.Status.PENDING).update(
            status=ApprovalRequestStep.Status.CANCELLED, updated_time=now
        )
        sync_current_level(approval, None)
    approval.status = ApprovalRequest.Status.CANCELLED
    invalidate_pending_count_cache()
    _emit_approval_event("approval.cancelled", approval)
    return True, None
