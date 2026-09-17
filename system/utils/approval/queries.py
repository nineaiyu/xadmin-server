#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批：待办计数与统计查询。"""

from .approvers import can_approve, get_approver_queryset
from .constants import APPROVAL_PENDING_COUNT_CACHE_SECONDS, APPROVAL_STATS_WINDOW_DAYS


def pending_count_for(user) -> int:
    """待我审批数（PENDING 且非本人发起；非审批人恒为 0），10s 短缓存。

    与「待我审批」页签同口径：本人发起的单在「我发起」页签处理，不计入待办，
    否则角标数与页签行数会不一致。
    """
    from django.core.cache import cache

    from system.models.approval import ApprovalRequest

    if not (user and getattr(user, "is_authenticated", False)):
        return 0
    if not (user.is_superuser or can_approve(user)):
        return 0

    def _load():
        return ApprovalRequest.objects.filter(status=ApprovalRequest.Status.PENDING).exclude(creator=user).count()

    return cache.get_or_set(f"approval_pending_count_{user.pk}", _load, APPROVAL_PENDING_COUNT_CACHE_SECONDS)


def invalidate_pending_count_cache():
    """失效各审批人的待办计数缓存（审批单状态变化后调用）。

    计数缓存按用户键存储，键空间 = 可审批人集合（超管或配置角色成员，规模有界），
    因此直接全量删除；否则角标会在 TTL 内与列表不一致（写操作后刷新读到的仍是旧值）。
    """
    from django.core.cache import cache

    try:
        pks = list(get_approver_queryset().values_list("pk", flat=True))
    except Exception:  # noqa: BLE001 审批人配置异常不影响主流程
        return
    if pks:
        cache.delete_many([f"approval_pending_count_{pk}" for pk in pks])


def approval_stats(user, days: int = APPROVAL_STATS_WINDOW_DAYS) -> dict:
    """审批统计（近 N 天）：我提交 / 我通过 / 我驳回 / 平均审批时长 / 我的待办。

    平均审批时长在 Python 侧求值（sqlite 对 DurationField 聚合支持不一，
    审批单量级小、窗口有界，遍历开销可忽略）。
    """
    import datetime

    from django.utils import timezone

    from system.models.approval import ApprovalRequest

    since = timezone.now() - datetime.timedelta(days=days)
    window = ApprovalRequest.objects.filter(created_time__gte=since)
    durations = [
        (approved_at - created_time).total_seconds()
        for created_time, approved_at in window.filter(approved_at__isnull=False).values_list(
            "created_time", "approved_at"
        )
        if created_time and approved_at
    ]
    return {
        "days": days,
        "submitted": window.filter(creator=user).count(),
        "approved": window.filter(approver=user, status=ApprovalRequest.Status.APPROVED).count(),
        "rejected": window.filter(approver=user, status=ApprovalRequest.Status.REJECTED).count(),
        "avg_approval_seconds": round(sum(durations) / len(durations)) if durations else None,
        "pending": pending_count_for(user),
    }
