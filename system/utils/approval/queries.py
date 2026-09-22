#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批：待办集合/计数与统计查询。"""

from .approvers import can_approve, get_approver_queryset
from .constants import APPROVAL_PENDING_COUNT_CACHE_SECONDS, APPROVAL_STATS_WINDOW_DAYS

PENDING_COUNT_KEY_PREFIX = "approval_pending_count_"


def pending_queryset_for(user):
    """待我审批集合（页签列表与角标计数的唯一口径来源，保证两者永远一致）。

    - 扁平单（current_level=0）：给「全局审批人」（超管或 APPROVAL_APPROVER_ROLES/PERMS）；
    - 多级链单（current_level>0）：只给当前级候选人（配置到谁就是谁，超管不越级）；
    - 本人发起的单一律排除（在「我发起」页签处理），否则角标数与页签行数不一致。

    非审批人（既非全局审批人、也不在任何在途链的当前级）查询返回空集，
    因此调用方无需再做前置资格判断。
    """
    from django.db.models import Q

    from system.models.approval import ApprovalRequest

    queryset = ApprovalRequest.objects.filter(status=ApprovalRequest.Status.PENDING).exclude(creator=user)
    condition = Q(current_level__gt=0, current_assignees=user)
    if user.is_superuser or can_approve(user):
        condition |= Q(current_level=0)
    return queryset.filter(condition).distinct()


def pending_count_for(user) -> int:
    """待我审批数（10s 短缓存）；与「待我审批」页签同口径（同一 queryset 函数）。"""
    from django.core.cache import cache

    if not (user and getattr(user, "is_authenticated", False)):
        return 0

    def _load():
        return pending_queryset_for(user).count()

    return cache.get_or_set(f"{PENDING_COUNT_KEY_PREFIX}{user.pk}", _load, APPROVAL_PENDING_COUNT_CACHE_SECONDS)


def invalidate_pending_count_cache():
    """失效待办计数缓存（审批单/级次状态变化后调用）。

    键空间 = 「可能被指派为审批人的用户」：多级链候选人可以是任意用户、无法
    完全枚举，故采用「枚举删除 + 前缀通配删除」双保险——
    - 枚举 = 全局审批人集合 ∪ 在途单当前级候选人（覆盖绝大多数命中）；
    - 通配 = django-redis 的 delete_pattern（测试环境不支持时静默降级，
      剩余偏差由 10s TTL 兜底，不会长期不一致）。
    """
    from django.core.cache import cache

    from system.models.approval import ApprovalRequest

    pks: set = set()
    try:
        pks |= set(get_approver_queryset().values_list("pk", flat=True))
    except Exception:  # noqa: BLE001 审批人配置异常不影响缓存清理
        pass
    try:
        pks |= set(
            ApprovalRequest.current_assignees.through.objects.filter(
                approvalrequest__status=ApprovalRequest.Status.PENDING
            ).values_list("userinfo_id", flat=True)
        )
    except Exception:  # noqa: BLE001 历史库/迁移中表缺失不影响主流程
        pass
    if pks:
        cache.delete_many([f"{PENDING_COUNT_KEY_PREFIX}{pk}" for pk in pks])
    try:
        cache.delete_pattern(f"{PENDING_COUNT_KEY_PREFIX}*")
    except Exception:  # noqa: BLE001 非 redis 后端无 delete_pattern
        pass


def approval_stats(user, days: int = APPROVAL_STATS_WINDOW_DAYS) -> dict:
    """审批统计（近 N 天）：我提交 / 我通过 / 我驳回 / 平均审批时长 / 我的待办。

    口径：approved/rejected 按「整单终态处理人」计数——多级链的中间级通过
    不计入「我通过」（终态审批人才代表最终放行/驳回该操作）。
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
