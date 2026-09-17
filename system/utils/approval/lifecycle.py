#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批：建单 / 消费令牌 / 状态流转。"""

import uuid

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

from common.core.response import ApiResponse

from .approved_actions import run_on_approved, snapshot_payload
from .approvers import build_module, find_active_pending, resolve_approvers
from .constants import APPROVAL_HEADER, APPROVAL_QUERY_PARAM
from .notify import (
    _emit_approval_event,
    forbidden_response,
    notify_applicant,
    notify_approvers,
    pending_response,
)
from .payload import canonical_params, get_request_object_pk, get_request_params, path_intercepted
from .queries import invalidate_pending_count_cache


def create_approval(view, request, module: str = ""):
    """建 PENDING 单并通知审批人；无可用审批人时直接报错（避免永久 PENDING）。

    module：调用方可显式指定审批单归属模块名（缺省取视图 docstring 首行），
    供非标准 CRUD 入口（如 AI 动作执行端点）给出可读的审批单标题。
    """
    from system.models.approval import ApprovalRequest

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
        creator=request.user,
    )
    invalidate_pending_count_cache()
    notify_approvers(approval, approvers)
    _emit_approval_event("approval.submitted", approval)
    return approval


def consume_approval(request, approval_id):
    """消费审批令牌：返回 None 表示放行（调用方继续执行业务），否则返回协议响应。"""
    from django.utils import timezone

    from system.models.approval import ApprovalRequest

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
    if not path_intercepted(request.path):
        return None

    approval_id = request.headers.get(APPROVAL_HEADER) or request.query_params.get(APPROVAL_QUERY_PARAM)
    if approval_id:
        return consume_approval(request, approval_id)

    approval = find_active_pending(request.user, request.method, request.path, get_request_params(request))
    if approval is None:
        approval = create_approval(view, request)
    return pending_response(approval)


def approve_request(approval, user):
    """审批通过：置 APPROVED + 令牌有效期（APPROVAL_TOKEN_TTL）。返回 (ok, detail)。

    状态流转以条件更新（CAS）落库：并发窗口内两个审批人各持同一 PENDING 快照时，
    先到者赢、后到者落空返回失败，不会覆盖先到者写下的终态（sqlite/MySQL 通用，
    不依赖 select_for_update）。
    """
    import datetime

    from django.utils import timezone

    from common.core.config import SysConfig
    from system.models.approval import ApprovalRequest

    if approval.status != ApprovalRequest.Status.PENDING:
        return False, _("Only pending requests can be approved")
    if approval.creator_id == user.pk:
        return False, _("The applicant cannot approve their own request")
    now = timezone.now()
    updated = ApprovalRequest.objects.filter(pk=approval.pk, status=ApprovalRequest.Status.PENDING).update(
        status=ApprovalRequest.Status.APPROVED,
        approver=user,
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


def reject_request(approval, user, reason: str):
    """驳回：reason 必填。返回 (ok, detail)。状态流转以 CAS 落库（同 approve_request）。"""
    from django.utils import timezone

    from system.models.approval import ApprovalRequest

    if approval.status != ApprovalRequest.Status.PENDING:
        return False, _("Only pending requests can be rejected")
    if approval.creator_id == user.pk:
        return False, _("The applicant cannot approve their own request")
    now = timezone.now()
    updated = ApprovalRequest.objects.filter(pk=approval.pk, status=ApprovalRequest.Status.PENDING).update(
        status=ApprovalRequest.Status.REJECTED,
        approver=user,
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


def cancel_request(approval, user):
    """申请人撤回：仅本人、仅 PENDING。返回 (ok, detail)。状态流转以 CAS 落库。"""
    from django.utils import timezone

    from system.models.approval import ApprovalRequest

    if approval.creator_id != user.pk:
        return False, _("Only the applicant can cancel the request")
    if approval.status != ApprovalRequest.Status.PENDING:
        return False, _("Only pending requests can be cancelled")
    updated = ApprovalRequest.objects.filter(pk=approval.pk, status=ApprovalRequest.Status.PENDING).update(
        status=ApprovalRequest.Status.CANCELLED, updated_time=timezone.now()
    )
    if not updated:
        approval.refresh_from_db(fields=["status"])
        return False, _("Only pending requests can be cancelled")
    approval.status = ApprovalRequest.Status.CANCELLED
    invalidate_pending_count_cache()
    _emit_approval_event("approval.cancelled", approval)
    return True, None
