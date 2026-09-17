#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全量审批流引擎：定时任务（超时提醒 / 终态实例清理）。"""

import datetime

from django.utils import timezone

from common.utils import get_logger

from .constants import FLOW_REMIND_CACHE_SECONDS, _models
from .engine import _notify

logger = get_logger(__name__)


def remind_pending_tasks(now=None) -> int:
    """超时提醒：节点 timeout_hours>0 且任务 PENDING 超时，向指派人补发一次（每任务每日一次）。"""
    from django.core.cache import cache

    ApprovalInstance, ApprovalNodeTask = _models().Instance, _models().Task

    now = now or timezone.now()
    reminded = 0
    queryset = (
        ApprovalNodeTask.objects.filter(
            status=ApprovalNodeTask.Status.PENDING,
            node__timeout_hours__gt=0,
            instance__status=ApprovalInstance.Status.PENDING,
            assignee__isnull=False,
        )
        .select_related("instance", "node", "assignee")
        .order_by("created_time")
    )
    for task in queryset.iterator():
        deadline = task.created_time + datetime.timedelta(hours=int(task.node.timeout_hours))
        if deadline > now:
            continue
        cache_key = f"approval_flow_remind_{task.pk}"
        if cache.get(cache_key):
            continue
        try:
            _notify([task.assignee], "remind", task.instance, extra=task.node_name)
            cache.set(cache_key, 1, FLOW_REMIND_CACHE_SECONDS)
            reminded += 1
        except Exception:  # noqa: BLE001 单条提醒失败不阻断其余任务
            logger.warning("send approval flow remind failed. task:%s", task.pk, exc_info=True)
    return reminded


def clean_finished_instances(keep_days: int = None, batch_size: int = 2000) -> int:
    """清理超过保留期的流程实例（APPROVAL_FLOW_KEEP_DAYS，默认 365 天；级联任务）。"""
    from django.db import transaction

    from common.core.config import SysConfig

    if keep_days is None:
        keep_days = int(SysConfig.APPROVAL_FLOW_KEEP_DAYS)
    if not keep_days or keep_days <= 0:
        return 0
    ApprovalInstance = _models().Instance

    deadline = timezone.now() - datetime.timedelta(days=keep_days)
    total = 0
    while True:
        pks = list(ApprovalInstance.objects.filter(created_time__lt=deadline).values_list("pk", flat=True)[:batch_size])
        if not pks:
            break
        with transaction.atomic():
            deleted, _rows = ApprovalInstance.objects.filter(pk__in=pks).delete()
        total += deleted
    return total
