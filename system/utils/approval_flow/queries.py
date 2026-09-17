#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全量审批流引擎：待办/可见域/统计查询口径。"""

import datetime

from django.db.models import Q
from django.utils import timezone

from .constants import FLOW_PENDING_COUNT_CACHE_SECONDS, FLOW_STATS_WINDOW_DAYS, _models


def pending_tasks_for(user):
    """待我审批的任务（PENDING、指派给我、且非本人发起）。"""
    ApprovalNodeTask = _models().Task

    return ApprovalNodeTask.objects.filter(status=ApprovalNodeTask.Status.PENDING, assignee=user).exclude(
        instance__creator=user
    )


def done_tasks_for(user):
    """我处理过的任务（actor=我），与「已办」页签同口径。"""
    ApprovalNodeTask = _models().Task

    return ApprovalNodeTask.objects.filter(actor=user)


def visible_instances_for(user):
    """实例可见域：超管全部；其余「我发起 ∪ 待我审批 ∪ 我参与过」。"""
    ApprovalInstance, ApprovalNodeTask = _models().Instance, _models().Task

    if user.is_superuser:
        return ApprovalInstance.objects.all()
    involved = ApprovalNodeTask.objects.filter(Q(assignee=user) | Q(actor=user)).values_list("instance_id", flat=True)
    return ApprovalInstance.objects.filter(Q(creator=user) | Q(pk__in=involved)).distinct()


def pending_count_for(user) -> int:
    """待我审批数（10s 短缓存；与「待办」页签同口径）。"""
    from django.core.cache import cache

    if not (user and getattr(user, "is_authenticated", False)):
        return 0

    def _load():
        return pending_tasks_for(user).count()

    return cache.get_or_set(f"approval_flow_pending_count_{user.pk}", _load, FLOW_PENDING_COUNT_CACHE_SECONDS)


def instance_stats(user, days: int = FLOW_STATS_WINDOW_DAYS) -> dict:
    """流程审批统计（近 N 天）：我提交 / 我通过 / 我驳回 / 我的待办。"""
    ApprovalInstance, ApprovalNodeTask = _models().Instance, _models().Task

    since = timezone.now() - datetime.timedelta(days=days)
    window = ApprovalInstance.objects.filter(created_time__gte=since)
    acted = ApprovalNodeTask.objects.filter(acted_at__gte=since, actor=user)
    return {
        "days": days,
        "submitted": window.filter(creator=user).count(),
        "approved": acted.filter(status=ApprovalNodeTask.Status.APPROVED).count(),
        "rejected": acted.filter(status=ApprovalNodeTask.Status.REJECTED).count(),
        "pending": pending_count_for(user),
    }
