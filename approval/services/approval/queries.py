#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批：待办集合/计数与统计查询。"""

from typing import Any

from .approvers import can_approve
from .constants import APPROVAL_PENDING_COUNT_CACHE_SECONDS, APPROVAL_STATS_WINDOW_DAYS

PENDING_COUNT_KEY_PREFIX = "approval_pending_count_"
#: 计数缓存版本号：状态变化只递增版本（O(1)），旧键随 10s TTL 自然淘汰，
#: 不再每次审批动作枚举审批人 + delete_pattern 全键扫描
PENDING_COUNT_VERSION_KEY = "approval_pending_count_version"


def pending_queryset_for(user: Any) -> Any:
    """待我审批集合（页签列表与角标计数的唯一口径来源，保证两者永远一致）。

    - 扁平单（current_level=0）：给「全局审批人」（超管或 APPROVAL_APPROVER_ROLES/PERMS）；
    - 多级链单（current_level>0）：只给当前级候选人（配置到谁就是谁，超管不越级）；
    - 本人发起的单一律排除（在「我发起」页签处理），否则角标数与页签行数不一致。

    非审批人（既非全局审批人、也不在任何在途链的当前级）查询返回空集，
    因此调用方无需再做前置资格判断。
    """
    from django.db.models import Q

    from approval.models.approval import ApprovalRequest

    queryset = ApprovalRequest.objects.filter(status=ApprovalRequest.Status.PENDING).exclude(creator=user)
    condition = Q(current_level__gt=0, current_assignees=user)
    if user.is_superuser or can_approve(user):
        condition |= Q(current_level=0)
    return queryset.filter(condition).distinct()


def pending_count_for(user: Any) -> int:
    """待我审批数（10s 短缓存）；与「待我审批」页签同口径（同一 queryset 函数）。"""
    from django.core.cache import cache

    if not (user and getattr(user, "is_authenticated", False)):
        return 0

    def _load() -> Any:
        return pending_queryset_for(user).count()

    try:
        version = int(cache.get(PENDING_COUNT_VERSION_KEY) or 0)
    except Exception:  # noqa: BLE001 缓存不可用按版本 0（仍可命中或回源）
        version = 0
    count: int = cache.get_or_set(
        f"{PENDING_COUNT_KEY_PREFIX}{version}_{user.pk}", _load, APPROVAL_PENDING_COUNT_CACHE_SECONDS
    )
    return count


def invalidate_pending_count_cache() -> None:
    """失效待办计数缓存（审批单/级次状态变化后调用）：仅递增版本号。

    旧实现枚举全部审批人 + 在途候选人，并对整个前缀做全键扫描；
    审批动作高频时全键扫描成本高。版本号方案为 O(1)，旧键由 10s TTL 清理。
    """
    from django.core.cache import cache

    try:
        cache.incr(PENDING_COUNT_VERSION_KEY)
    except ValueError:
        cache.set(PENDING_COUNT_VERSION_KEY, 1, None)
    except Exception:  # noqa: BLE001 缓存不可用时由短 TTL 兜底
        return


def approval_stats(user: Any, days: int = APPROVAL_STATS_WINDOW_DAYS) -> dict[str, Any]:
    """审批统计（近 N 天）：我提交 / 我通过 / 我驳回 / 平均审批时长 / 我的待办。

    口径：approved/rejected 按「整单终态处理人」计数——多级链的中间级通过
    不计入「我通过」（终态审批人才代表最终放行/驳回该操作）。
    平均审批时长只统计**通过**的单（``approved_at`` 在驳回时也会落值作处理时点，
    直接用它会把驳回/失败单计入审批耗时，与「平均审批时长」语义不符）。
    """
    import datetime

    from django.utils import timezone

    from approval.models.approval import ApprovalRequest

    since = timezone.now() - datetime.timedelta(days=days)
    window = ApprovalRequest.objects.filter(created_time__gte=since)
    durations = [
        (approved_at - created_time).total_seconds()
        for created_time, approved_at in window.filter(
            status=ApprovalRequest.Status.APPROVED, approved_at__isnull=False
        ).values_list("created_time", "approved_at")
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
