#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全量审批流引擎：定时任务（超时提醒与自动动作 / 卡死单兜底清理 / 终态实例清理）。"""

import datetime

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

from .constants import FLOW_REMIND_CACHE_SECONDS, _models
from .engine import (
    _cancel_pending_tasks,
    _finish_instance,
    _invalidate_pending_count,
    _notify,
    _settle_node_after_approve,
)

logger = get_logger(__name__)

#: 卡死单判定门槛（分钟）：PENDING 且无任何节点任务、且创建超过该时长
STUCK_INSTANCE_TIMEOUT_MINUTES = 30

#: 升级转交无可用 leader 时的重试节流（秒）：24h 内不重复尝试（提醒链路照常运行）
TIMEOUT_SKIP_RETRY_SECONDS = 60 * 60 * 24

#: 系统代处理的 actor 展示名快照（超时自动通过/驳回落轨迹用）
_SYSTEM_ACTOR_DISPLAY = str(_("System (timeout)"))


def remind_pending_tasks(now=None) -> int:
    """超时提醒：节点 timeout_hours>0 且任务 PENDING 超时，向指派人补发一次（每任务每日一次）。"""
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
    （异常 / 历史脏数据）：这类单无人可处理、会长期挂在在途列表，必须由兜底任务收敛。
    终态跃迁复用 _finish_instance（CAS + Webhook + 业务回调 + 申请人通知，只发生一次）。
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


def _timed_out_task_deadline(task, now) -> bool:
    """任务是否已过节点超时线（与提醒同口径：created_time + timeout_hours）。"""
    return task.created_time + datetime.timedelta(hours=int(task.node.timeout_hours)) <= now


def _auto_approve_task(task, instance, now) -> bool:
    """超时自动通过单条任务（系统代处理：actor 置空、comment 注明），并按节点策略结算。"""
    ApprovalNodeTask = _models().Task

    updated = ApprovalNodeTask.objects.filter(pk=task.pk, status=ApprovalNodeTask.Status.PENDING).update(
        status=ApprovalNodeTask.Status.APPROVED,
        actor=None,
        actor_display=_SYSTEM_ACTOR_DISPLAY,
        comment=str(_("Auto approved on timeout")),
        acted_at=now,
        updated_time=now,
    )
    if not updated:
        return False
    # 与人工通过同口径结算（或签推进 / 会签齐票 / 比例达标），失效集含原处理人
    invalidated = _settle_node_after_approve(instance, task.node)
    invalidated.add(task.assignee_id)
    _invalidate_pending_count(invalidated)
    if task.assignee_id:
        _notify([task.assignee], "timeout", instance, extra=task.node_name)
    return True


def _auto_reject_task(task, instance, now) -> bool:
    """超时自动驳回：任务置 REJECTED、整单终态 REJECTED（原因注明超时自动驳回）。"""
    ApprovalInstance, ApprovalNodeTask = _models().Instance, _models().Task

    reason = str(_("Auto rejected on timeout"))
    updated = ApprovalNodeTask.objects.filter(pk=task.pk, status=ApprovalNodeTask.Status.PENDING).update(
        status=ApprovalNodeTask.Status.REJECTED,
        actor=None,
        actor_display=_SYSTEM_ACTOR_DISPLAY,
        comment=reason[:255],
        acted_at=now,
        updated_time=now,
    )
    if not updated:
        return False
    invalidated = {task.assignee_id} if task.assignee_id else set()
    invalidated.update(_cancel_pending_tasks(instance))
    if _finish_instance(instance, ApprovalInstance.Status.REJECTED, reason):
        _notify([instance.creator], "rejected", instance, extra=reason)
    _invalidate_pending_count(invalidated)
    return True


def _auto_transfer_up(task, instance, now) -> bool:
    """超时升级转交：任务转给处理人所在部门的 leader（delegate_from 记原处理人）。

    无部门 / 无 leader / leader 停用 / leader 即本人或申请人时本轮跳过（24h 重试节流），
    待办保持 PENDING、提醒链路照常运行——升级是增益动作，不因目标缺失而卡死或放行。
    """
    ApprovalNodeTask = _models().Task

    leader = getattr(getattr(task.assignee, "dept", None), "leader", None)
    if leader is None or not leader.is_active or leader.pk in {task.assignee_id, instance.creator_id}:
        cache_key = f"approval_flow_timeout_skip_{task.pk}"
        if not cache.add(cache_key, 1, TIMEOUT_SKIP_RETRY_SECONDS):
            return False
        logger.warning(
            "approval flow timeout escalation skipped (no eligible leader). task:%s assignee:%s",
            task.pk,
            task.assignee_id,
        )
        return False

    note = str(_("Escalated to {} on timeout")).format(getattr(leader, "nickname", "") or leader.username)
    updated = ApprovalNodeTask.objects.filter(pk=task.pk, status=ApprovalNodeTask.Status.PENDING).update(
        status=ApprovalNodeTask.Status.CANCELLED,
        comment=note[:255],
        updated_time=now,
    )
    if not updated:
        return False
    ApprovalNodeTask.objects.create(
        instance=instance,
        node=task.node,
        node_name=task.node_name,
        node_order=task.node_order,
        assignee=leader,
        assignee_display=str(getattr(leader, "nickname", "") or leader.username),
        delegate_from=task.assignee,
        comment=str(_("Auto escalated on timeout")),
    )
    _notify([leader], "transferred", instance, extra=str(_("Auto escalated on timeout")))
    _invalidate_pending_count({leader.pk, task.assignee_id})
    return True


def execute_timeout_actions(now=None, batch_size: int = 200) -> dict:
    """节点超时自动动作：timeout_action 非空的 PENDING 任务到点后由系统按分支处理。

    分支（ApprovalFlowNode.TimeoutAction）：
    - ``approve``：系统自动通过该任务，节点结算与人工通过同口径（或签推进 / 会签齐票 /
      比例达标）；会签场景其余候选人仍需处理，不整单放行；
    - ``reject``：系统自动驳回整单（终态 REJECTED，Webhook/业务回调/申请人通知齐全）；
    - ``transfer_up``：任务升级转交给处理人所在部门的 leader（新增任务记 delegate_from，
      时间线标注「由 X 代理」）；无可用 leader 时跳过并按 24h 节流重试。

    并发安全：逐任务在实例行锁（select_for_update）内重新校验状态后再 CAS 落子，与
    人工审批互斥；动作完成后任务离开 PENDING，天然不会重复触发。到点判定与提醒同口径
    （created_time + timeout_hours）。
    """
    ApprovalInstance, ApprovalNodeTask, ApprovalFlowNode = _models().Instance, _models().Task, _models().Node
    TimeoutAction = ApprovalFlowNode.TimeoutAction

    now = now or timezone.now()
    counts = {"approve": 0, "reject": 0, "transfer_up": 0}
    queryset = (
        ApprovalNodeTask.objects.filter(
            status=ApprovalNodeTask.Status.PENDING,
            node__timeout_hours__gt=0,
            instance__status=ApprovalInstance.Status.PENDING,
            assignee__isnull=False,
        )
        .exclude(node__timeout_action=TimeoutAction.NONE)
        .select_related("instance", "node", "assignee", "assignee__dept")
        .order_by("created_time")[:batch_size]
    )
    for task in queryset:
        if not _timed_out_task_deadline(task, now):
            continue
        action = task.node.timeout_action
        with transaction.atomic():
            # 锁实例行并复核：并发下实例可能已被人工处理（推进/终结）
            instance = (
                ApprovalInstance.objects.select_for_update()
                .filter(pk=task.instance_id, status=ApprovalInstance.Status.PENDING)
                .first()
            )
            if instance is None:
                continue
            if action == TimeoutAction.APPROVE:
                handled = _auto_approve_task(task, instance, now)
            elif action == TimeoutAction.REJECT:
                handled = _auto_reject_task(task, instance, now)
            else:
                handled = _auto_transfer_up(task, instance, now)
        if handled:
            counts[action] = counts.get(action, 0) + 1
            logger.info("approval flow timeout action executed. task:%s action:%s", task.pk, action)
    return counts
