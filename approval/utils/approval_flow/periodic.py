#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全量审批流引擎：定时任务（超时提醒 / 卡死单兜底清理 / 终态实例清理）。"""

import datetime

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

from .constants import FLOW_REMIND_CACHE_SECONDS, _models
from .engine import _finish_instance, _notify

logger = get_logger(__name__)

#: 卡死单判定门槛（分钟）：PENDING 且无任何节点任务、且创建超过该时长
STUCK_INSTANCE_TIMEOUT_MINUTES = 30


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


def cancel_stuck_instances(timeout_minutes: int = STUCK_INSTANCE_TIMEOUT_MINUTES, batch_size: int = 200) -> int:
    """兜底清理卡死单：PENDING 且无任何节点任务、且创建超过 N 分钟 → CANCELLED。

    正常发起必然是「实例 + 首节点任务」同时落库（见 create_instance 的事务边界；
    业务接入方如请假提交也已事务化），出现无任务的 PENDING 实例说明发起链路曾中断
    （异常 / 历史脏数据）：这类单无人可处理，且会锁住流程改版（存在在途实例时禁止
    改动节点），必须由兜底任务收敛。终态跃迁复用 _finish_instance（CAS + Webhook +
    业务回调 + 申请人通知，只发生一次）。
    """
    ApprovalInstance, ApprovalNodeTask = _models().Instance, _models().Task

    now = timezone.now()
    deadline = now - datetime.timedelta(minutes=max(1, int(timeout_minutes or 0)))
    pks = list(
        ApprovalInstance.objects.filter(status=ApprovalInstance.Status.PENDING, created_time__lt=deadline)
        .exclude(pk__in=ApprovalNodeTask.objects.values("instance_id"))
        .values_list("pk", flat=True)[:batch_size]
    )
    if not pks:
        return 0

    cancelled = 0
    reason = str(_("The application was cancelled automatically: the submission did not complete"))
    for instance in ApprovalInstance.objects.filter(pk__in=pks).select_related("creator"):
        if not _finish_instance(instance, ApprovalInstance.Status.CANCELLED, reason):
            continue  # 已被并发处理（撤回/推进），跳过
        cancelled += 1
        # 通知文案取 instance.reason（_finish_instance 已把原因写入），不走 extra
        _notify([instance.creator], "cancelled", instance)
        logger.warning(
            "cancel stuck approval instance (pending without tasks). instance:%s created:%s",
            instance.pk,
            instance.created_time,
        )
    return cancelled


def clean_finished_instances(keep_days: int | None = None, batch_size: int = 2000) -> int:
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
