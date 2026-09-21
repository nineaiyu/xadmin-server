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


def node_progress_for(instance, node=None, tasks=None) -> dict | None:
    """当前（或指定）节点进度：比例会签的「达标线预览」，会签/或签也给可视化数字。

    返回 ``{approve_type, approve_ratio, total, approved, pending, rejected, required, reached}``：
    - ``total``：节点全部候选任务数（加签后随之抬升，故达标线预览对加签决策有用）；
    - ``required``：达标所需通过数——RATIO = ceil(total × ratio / 100)（与 engine 判定同源），
      AND = total，OR = 1；
    - 非审批中实例或节点无任务 → None（前端不渲染进度块）。

    ``tasks`` 可传入已预取的任务列表（序列化器路径复用 prefetch，避免列表页 N+1）。
    """
    ApprovalInstance, ApprovalNodeTask = _models().Instance, _models().Task

    node = node or instance.current_node
    if instance.status != ApprovalInstance.Status.PENDING or node is None:
        return None
    if tasks is None:
        tasks = list(ApprovalNodeTask.objects.filter(instance=instance, node=node))
    else:
        tasks = [task for task in tasks if task.node_id == node.pk]
    total = len(tasks)
    if not total:
        return None
    approved = sum(1 for task in tasks if task.status == ApprovalNodeTask.Status.APPROVED)
    pending = sum(1 for task in tasks if task.status == ApprovalNodeTask.Status.PENDING)
    rejected = sum(1 for task in tasks if task.status == ApprovalNodeTask.Status.REJECTED)
    approve_type = node.approve_type
    if approve_type == node.ApproveType.RATIO:
        required = -(-total * (node.approve_ratio or 100) // 100)  # ceil，与 engine 同口径
    elif approve_type == node.ApproveType.OR:
        required = 1
    else:
        required = total
    return {
        "approve_type": approve_type,
        "approve_ratio": node.approve_ratio or 100,
        "total": total,
        "approved": approved,
        "pending": pending,
        "rejected": rejected,
        "required": required,
        "reached": approved >= required,
    }


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
