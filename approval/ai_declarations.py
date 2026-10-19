#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批域与请假业务的 AI 动作声明。

- 审批中心动作复用既有查询/裁决接口（决定类动作走 AI 层 412 强制审批）；
- ``leave.submit`` 是内置动作：校验与提交复用本应用的 ``validate_leave_payload`` /
  ``submit_leave``，不再由 AI 应用复制一份业务规则；
- 请假单接口随 ``approval_flow`` 模块裁剪，审批单接口随 ``approval`` 模块裁剪。
"""

import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.utils.translation import gettext_lazy as _

from ai.utils.ai_action_types import MAX_REASON_LENGTH, ActionSpec, always_available, never_requires_approval
from ai.utils.ai_api_actions import IN_QUERY, api_action, requires_approval_high_risk


def _parse_date(value: Any) -> Any:
    try:
        return datetime.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _parse_decimal(value: Any) -> Any:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _validate_leave(user: Any, params: dict[str, Any]) -> Any:
    """校验请假参数：返回 (JSON 安全的规范化参数, 错误文案)。"""
    from approval.models.leave import Leave
    from approval.utils.leave import leave_days, validate_leave_payload

    allowed_types = {choice[0] for choice in Leave.LeaveType.choices}
    leave_type = str(params.get("leave_type") or Leave.LeaveType.ANNUAL).strip()
    if leave_type not in allowed_types:
        return {}, str(_("Unknown leave type: {}").format(leave_type))
    start_date = _parse_date(params.get("start_date"))
    end_date = _parse_date(params.get("end_date"))
    if not start_date or not end_date:
        return {}, str(_("Start date and end date must be in YYYY-MM-DD format"))
    reason = str(params.get("reason") or "").strip()[:MAX_REASON_LENGTH]
    if not reason:
        return {}, str(_("Reason is required"))
    days = None
    raw_days = params.get("days")
    if raw_days not in (None, ""):
        days = _parse_decimal(raw_days)
        if days is None:
            return {}, str(_("Days must be a number"))
    error = validate_leave_payload(start_date=start_date, end_date=end_date, days=days, creator=user)
    if error:
        return {}, error
    if days is None:
        days = leave_days(start_date, end_date)
    return (
        {
            "leave_type": leave_type,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "days": float(days),
            "reason": reason,
        },
        None,
    )


def _execute_leave(user: Any, params: dict[str, Any]) -> dict[str, Any]:
    """创建请假单并立即提交审批（无流程/无审批人时保留草稿，与业务页面同口径）。"""
    from approval.models.leave import Leave
    from approval.utils.leave import submit_leave

    leave = Leave.objects.create(
        leave_type=params["leave_type"],
        start_date=_parse_date(params["start_date"]),
        end_date=_parse_date(params["end_date"]),
        days=_parse_decimal(params["days"]),
        reason=params["reason"],
        creator=user,
        modifier=user,
        dept_belong=getattr(user, "dept", None),
    )
    ok, error = submit_leave(leave, user)
    leave.refresh_from_db()
    data = {"leave_id": str(leave.pk), "status": leave.status}
    if not ok:
        return {"ok": True, "detail": str(_("Saved as draft: {}").format(error)), "data": data}
    return {"ok": True, "detail": str(_("The leave request has been submitted for approval")), "data": data}


AI_ACTIONS = {
    # ---- 审批中心（只读查询 + 高危裁决） ----
    "approval.list": api_action(
        key="approval.list",
        label=_("List approval requests"),
        description=_("Approval requests visible to the caller (mine to approve / submitted by me), newest first"),
        method="GET",
        path="/api/approval/approvals",
        params={
            "status": {
                "type": "string",
                "required": False,
                "in": IN_QUERY,
                "description": "Filter by status keyword (pending/approved/rejected/cancelled/expired/failed)",
            }
        },
        modules=("approval",),
    ),
    "approval.pending_count": api_action(
        key="approval.pending_count",
        label=_("Count my pending approvals"),
        description=_("Number of approval requests waiting for me to review"),
        method="GET",
        path="/api/approval/approvals/pending-count",
        params={},
        modules=("approval",),
    ),
    "approval.stats": api_action(
        key="approval.stats",
        label=_("Show approval statistics"),
        description=_("Approval stats of recent 30 days (submitted/approved/rejected, average duration, my pending)"),
        method="GET",
        path="/api/approval/approvals/stats",
        params={},
        modules=("approval",),
    ),
    "approval.approve": api_action(
        key="approval.approve",
        label=_("Approve an approval request"),
        description=_(
            "Approve one approval request as its reviewer (destructive decision; goes through the approval "
            "flow itself). Request id comes from approval.list; the applicant cannot approve their own request"
        ),
        method="POST",
        path="/api/approval/approvals/<pk>/approve",
        params={
            "pk": {
                "type": "pk",
                "required": True,
                "in": "path",
                "description": "Approval request id (from approval.list)",
            }
        },
        requires_approval=requires_approval_high_risk,
        modules=("approval",),
    ),
    "approval.reject": api_action(
        key="approval.reject",
        label=_("Reject an approval request"),
        description=_(
            "Reject one approval request with a mandatory reason (destructive decision; goes through the "
            "approval flow itself). Request id comes from approval.list"
        ),
        method="POST",
        path="/api/approval/approvals/<pk>/reject",
        params={
            "pk": {
                "type": "pk",
                "required": True,
                "in": "path",
                "description": "Approval request id (from approval.list)",
            },
            "reason": {"type": "string", "required": True, "in": "body", "description": "Rejection reason"},
        },
        requires_approval=requires_approval_high_risk,
        modules=("approval",),
    ),
    # ---- 审批流：实例 / 流程定义 / 委托 / 请假（只读） ----
    "approval_instance.list": api_action(
        key="approval_instance.list",
        label=_("List approval flow instances"),
        description=_("Approval flow instances visible to the caller (title, flow, status, current node)"),
        method="GET",
        path="/api/approval/approval-instances",
        params={
            "status": {
                "type": "string",
                "required": False,
                "in": IN_QUERY,
                "description": "Filter by instance status keyword",
            }
        },
        modules=("approval_flow",),
    ),
    "approval_instance.detail": api_action(
        key="approval_instance.detail",
        label=_("Show an approval flow instance"),
        description=_("One approval flow instance with form data, node trail and comments"),
        method="GET",
        path="/api/approval/approval-instances/<pk>",
        params={
            "pk": {
                "type": "pk",
                "required": True,
                "in": "path",
                "description": "Instance id (from approval_instance.list)",
            }
        },
        modules=("approval_flow",),
    ),
    "approval_instance.pending_count": api_action(
        key="approval_instance.pending_count",
        label=_("Count my pending flow tasks"),
        description=_("Number of approval flow tasks waiting for the current user"),
        method="GET",
        path="/api/approval/approval-instances/pending-count",
        params={},
        modules=("approval_flow",),
    ),
    "approval_flow.list": api_action(
        key="approval_flow.list",
        label=_("List approval flow definitions"),
        description=_("Approval flow definitions (name, code, enabled state, current version)"),
        method="GET",
        path="/api/approval/approval-flows",
        params={},
        modules=("approval_flow",),
    ),
    "approval_delegation.list": api_action(
        key="approval_delegation.list",
        label=_("List approval delegations"),
        description=_("Approval delegations (delegator, delegate, flow scope, time range)"),
        method="GET",
        path="/api/approval/approval-delegations",
        params={},
        modules=("approval_flow",),
    ),
    "leave.list": api_action(
        key="leave.list",
        label=_("List leave requests"),
        description=_("Leave requests visible to the caller (type, date range, days, status)"),
        method="GET",
        path="/api/approval/leaves",
        params={
            "status": {
                "type": "string",
                "required": False,
                "in": IN_QUERY,
                "description": "Filter by leave status keyword",
            }
        },
        modules=("approval_flow",),
    ),
    # ---- 请假申请（内置动作：校验/提交在本应用内） ----
    "leave.submit": ActionSpec(
        key="leave.submit",
        label=_("Submit a leave request"),
        description=_("Create a leave request for the current user and submit it into the leave approval flow"),
        params={
            "leave_type": {
                "type": "string",
                "required": False,
                "enum": ["annual", "sick", "personal", "comp_time", "marriage", "other"],
                "description": "Leave type, default annual",
            },
            "start_date": {"type": "string", "required": True, "description": "Start date, YYYY-MM-DD"},
            "end_date": {"type": "string", "required": True, "description": "End date, YYYY-MM-DD"},
            "days": {"type": "number", "required": False, "description": "Days, default the full range"},
            "reason": {"type": "string", "required": True, "description": "Reason, max 500 chars"},
        },
        required_visits=(("POST", "/api/approval/leaves"),),
        validate=_validate_leave,
        execute=_execute_leave,
        requires_approval=never_requires_approval,
        available=always_available,
        modules=("approval_flow",),
    ),
}
