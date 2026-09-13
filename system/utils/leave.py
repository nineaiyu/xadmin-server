#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""请假业务与审批流引擎的挂接逻辑（ADR-032）。

三段职责：
1. 校验：日期合法性、天数与跨度一致、同一申请人假期区间不重叠（serializer 与
   提交前各校验一次，避免绕过接口写入脏数据）；
2. 提交：解析请假审批流程 → ``create_instance``（带 biz_type/biz_id 绑定）→
   业务单置 PENDING 并挂上实例；
3. 回写：实例终态经 ``approval_instance_finished`` 信号回到 ``sync_leave_instance``，
   把 APPROVED/REJECTED/CANCELLED 同步到业务单状态。
"""

import datetime
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.core.config import SysConfig
from common.utils import get_logger

logger = get_logger(__name__)

# 业务标识：ApprovalInstance.biz_type / approval_instance_finished 分发键
LEAVE_BIZ_TYPE = "leave"
# 业务单上「未结束」的状态：这些状态下才有区间冲突与可提交语义
LEAVE_OPEN_STATUSES = ("DRAFT", "PENDING", "APPROVED")


def _models():
    """延迟导入模型：避免 system.models ↔ system.utils 导入期循环依赖。"""
    from system.models.leave import Leave

    return Leave


def leave_days(start_date, end_date) -> Decimal:
    """按自然日计算天数（含首尾，最小 1 天）。"""
    if not start_date or not end_date:
        return Decimal("0")
    span = (end_date - start_date).days + 1
    return Decimal(max(span, 0))


def validate_leave_payload(*, start_date, end_date, days=None, creator=None, exclude_pk=None) -> str:
    """业务校验：返回错误文案，通过返回 None。

    - 结束日期不得早于开始日期；
    - 天数必须 > 0 且不超过起止跨度（允许半天等不足整天的申请）；
    - 同一申请人不得存在区间重叠的未结束请假单（含草稿与审批中）。
    """
    if not start_date or not end_date:
        return str(_("Start date and end date are required"))
    if end_date < start_date:
        return str(_("End date cannot be earlier than start date"))
    span = leave_days(start_date, end_date)
    amount = Decimal(str(days)) if days not in (None, "") else span
    if amount <= 0:
        return str(_("Days must be greater than 0"))
    if amount > span:
        return str(_("Days cannot exceed the date range (max {} days)").format(span))

    if creator is None:
        return None
    Leave = _models()
    overlapped = (
        Leave.objects.filter(
            creator=creator,
            status__in=LEAVE_OPEN_STATUSES,
            start_date__lte=end_date,
            end_date__gte=start_date,
        )
        .exclude(pk=exclude_pk)
        .first()
    )
    if overlapped is not None:
        return str(_("A leave request for {} already overlaps with this period").format(overlapped.approval_title))
    return None


def resolve_leave_flow(leave_type: str = ""):
    """解析请假审批流程：配置 code 优先 → ``leave_<type>`` → ``leave`` 前缀的启用流程。

    找不到返回 None（调用方拒绝提交并提示管理员配置流程，而不是静默直通）。
    """
    from system.models.approval import ApprovalFlow

    candidates = [str(SysConfig.LEAVE_APPROVAL_FLOW_CODE or "").strip()]
    if leave_type:
        candidates.append(f"leave_{leave_type}")
    for code in candidates:
        if not code:
            continue
        flow = ApprovalFlow.objects.filter(code=code, is_active=True).first()
        if flow is not None:
            return flow
    return ApprovalFlow.objects.filter(is_active=True, code__startswith="leave").order_by("pk").first()


def submit_leave(leave, user):
    """提交请假申请（发起审批）：返回 (ok, detail)。

    仅 DRAFT / REJECTED / CANCELLED 可提交（PENDING 在途、APPROVED 已批准不可重提）；
    提交成功业务单置 PENDING 并绑定流程实例，后续状态由信号回写。
    """
    Leave = _models()
    if leave.creator_id is None:
        leave.creator = user
    if leave.creator_id != user.pk and not getattr(user, "is_superuser", False):
        return False, str(_("Only the applicant can submit this request"))
    if leave.status == Leave.Status.PENDING:
        return False, str(_("The request is already in approval"))
    if leave.status == Leave.Status.APPROVED:
        return False, str(_("The request has been approved"))

    error = validate_leave_payload(
        start_date=leave.start_date,
        end_date=leave.end_date,
        days=leave.days,
        creator=leave.creator,
        exclude_pk=leave.pk,
    )
    if error:
        return False, error

    flow = resolve_leave_flow(leave.leave_type)
    if flow is None:
        return False, str(_("No leave approval flow is configured, please contact the administrator"))

    # create_instance 的 form_data 会做 JSON 序列化，故这里做一次 ROUND_TRIP 校验，
    # 避免 Decimal/date 直接进 JSONField 时悄悄降级（引擎条件节点按 key 取这些值）
    form_data = leave.form_data

    from system.utils.approval_flow import create_instance

    instance, error = create_instance(
        flow=flow,
        applicant=leave.creator,
        title=leave.approval_title,
        form_data=form_data,
        biz_type=LEAVE_BIZ_TYPE,
        biz_id=str(leave.pk),
    )
    if error:
        return False, error

    leave.instance = instance
    leave.status = Leave.Status.PENDING
    leave.modifier = user
    leave.save(update_fields=["instance", "status", "modifier", "updated_time"])
    return True, None


def cancel_leave(leave, user):
    """撤回请假申请：仅申请人、仅审批中；撤回后业务单置 CANCELLED。返回 (ok, detail)。"""
    Leave = _models()
    if leave.status != Leave.Status.PENDING:
        return False, str(_("Only pending requests can be cancelled"))
    if leave.creator_id != user.pk:
        return False, str(_("Only the applicant can cancel the request"))
    if leave.instance_id is None:
        # 数据异常兜底：没有实例却停在 PENDING，直接复位为撤回，避免卡死
        leave.status = Leave.Status.CANCELLED
        leave.save(update_fields=["status", "updated_time"])
        return True, None

    from system.utils.approval_flow import cancel_instance

    ok, detail = cancel_instance(leave.instance, user)
    if not ok:
        return False, detail
    # 正常路径由 approval_instance_finished 信号回写；这里兜底幂等（信号异常时仍能收敛）
    sync_leave_instance(leave.instance, Leave.Status.CANCELLED)
    return True, None


def sync_leave_instance(instance, status, reason: str = "") -> None:
    """审批终态回写业务单：由 system/signal_handler.py 的接收器调用（幂等）。"""
    from system.models.approval import ApprovalInstance

    if getattr(instance, "biz_type", "") != LEAVE_BIZ_TYPE or not instance.biz_id:
        return
    Leave = _models()
    status = str(status)
    if status not in dict(ApprovalInstance.Status.choices):
        return
    leave = Leave.objects.filter(pk=instance.biz_id).first()
    if leave is None:
        logger.warning("leave sync skipped, business row missing. instance:%s biz_id:%s", instance.pk, instance.biz_id)
        return
    if leave.status == status:
        return
    leave.status = status
    leave.modifier = instance.creator
    leave.save(update_fields=["status", "modifier", "updated_time"])
    logger.info("leave status synced by approval instance. leave:%s status:%s reason:%s", leave.pk, status, reason)


def leave_conflict_queryset(queryset, user):
    """列表可见域：超管全部；其余「我提交 ∪ 我审批过（待办/已办）」。"""
    from django.db.models import Q

    if user.is_superuser:
        return queryset
    return queryset.filter(
        Q(creator=user) | Q(instance__tasks__assignee=user) | Q(instance__tasks__actor=user)
    ).distinct()


def pending_leave_tasks_for(user):
    """待我审批的请假任务（供发起人视角之外的提醒场景复用，与引擎口径一致）。"""
    from system.models.approval import ApprovalNodeTask

    Leave = _models()
    leave_pks = ApprovalNodeTask.objects.filter(
        status=ApprovalNodeTask.Status.PENDING,
        assignee=user,
        instance__biz_type=LEAVE_BIZ_TYPE,
    ).values_list("instance__biz_id", flat=True)
    return Leave.objects.filter(pk__in=list(leave_pks))


def leave_stats(user, days: int = 30) -> dict:
    """我的请假统计（近 N 天）：提交数 / 审批中 / 已通过 / 已驳回。"""
    Leave = _models()
    since = timezone.now() - datetime.timedelta(days=days)
    queryset = Leave.objects.filter(creator=user, created_time__gte=since)
    return {
        "days": days,
        "submitted": queryset.count(),
        "pending": queryset.filter(status=Leave.Status.PENDING).count(),
        "approved": queryset.filter(status=Leave.Status.APPROVED).count(),
        "rejected": queryset.filter(status=Leave.Status.REJECTED).count(),
    }
