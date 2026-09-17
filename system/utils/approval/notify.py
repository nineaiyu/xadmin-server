#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批：通知、Webhook 事件与协议响应。"""

from django.utils.translation import gettext_lazy as _

from common.core.response import ApiResponse
from common.utils import get_logger

from .constants import (
    APPROVAL_NOTIFY_THROTTLE_SECONDS,
    APPROVAL_PENDING_CODE,
    APPROVAL_RESPONSE_TYPE,
)

logger = get_logger(__name__)


def notify_approvers(approval, approvers):
    """通知全部审批人（60s 节流，防重复提交刷屏）。"""
    from django.core.cache import cache

    from system.notifications import ApprovalRequestMessage

    if not cache.add(f"approval_notify_{approval.pk}", 1, APPROVAL_NOTIFY_THROTTLE_SECONDS):
        return
    for user in approvers:
        try:
            ApprovalRequestMessage(user, "submitted", approval).publish(is_async=True)
        except Exception:
            logger.warning("send approval notify failed. approval:%s user:%s", approval.pk, user.pk, exc_info=True)


def _emit_approval_event(event: str, approval) -> None:
    """出站 Webhook：审批事件（emit 全程吞异常，不影响审批流转）。"""
    from system.utils.webhook import emit_webhook_event

    try:
        emit_webhook_event(
            event,
            {
                "approval_id": str(approval.pk),
                "module": approval.module,
                "path": approval.path,
                "status": approval.status,
                "creator": getattr(approval.creator, "username", ""),
            },
        )
    except Exception:  # noqa: BLE001 双保险（emit 自身已吞异常）
        logger.warning("emit approval webhook failed: %s", event, exc_info=True)


def notify_applicant(approval, event: str):
    """向申请人推送审批结果（通过/驳回）。"""
    from system.notifications import ApprovalRequestMessage

    if not approval.creator:
        return
    try:
        ApprovalRequestMessage(approval.creator, event, approval).publish(is_async=True)
    except Exception:
        logger.warning("send approval result failed. approval:%s event:%s", approval.pk, event, exc_info=True)


def pending_response(approval) -> ApiResponse:
    """待审批协议响应：HTTP 412 + 业务码 1002 + type=approval_required。"""
    return ApiResponse(
        code=APPROVAL_PENDING_CODE,
        status=412,
        detail=_("Operation submitted for approval (No. {}), please retry after it is approved").format(
            str(approval.pk)[:8].upper()
        ),
        data={"approval_id": str(approval.pk), "status": approval.status},
        type=APPROVAL_RESPONSE_TYPE,
    )


def forbidden_response(detail: str) -> ApiResponse:
    return ApiResponse(code=403, status=403, detail=detail, type=APPROVAL_RESPONSE_TYPE)
