#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全量审批流引擎：实例推进（发起 / 通过 / 驳回 / 撤回 / 加签）。"""

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

from .conditions import (
    _split_values,
    next_node,
    resolve_assignee_pairs,
    resolve_assignees,
    simulate_path,
    validate_form,
)
from .constants import _FLOW_FINISH_EVENTS, _models, _users

logger = get_logger(__name__)

# 人工催办节流窗口（秒）：同一实例内申请人在窗口内重复催办只发一次通知
URGE_THROTTLE_SECONDS = 600


def _notify(users, event, instance, extra=None):
    """向用户列表推送流程通知（单条失败只记日志，不阻断推进）。"""
    from system.notifications import ApprovalFlowMessage

    for user in users:
        if not user:
            continue
        try:
            ApprovalFlowMessage(user, event, instance, extra=extra).publish(is_async=True)
        except Exception:  # noqa: BLE001 通知链路故障不影响审批主流程
            logger.warning("send approval flow notify failed. instance:%s user:%s", instance.pk, user.pk, exc_info=True)


def _invalidate_pending_count(users=None):
    """失效待办计数缓存：默认全量（规模有界），亦可指定受影响用户。"""
    from django.core.cache import cache

    if users is None:
        UserInfo = _users()
        try:
            pks = list(UserInfo.objects.filter(is_active=True).values_list("pk", flat=True)[:5000])
        except Exception:  # noqa: BLE001 计数缓存异常不影响主流程
            return
    else:
        pks = [user.pk for user in users]
    if pks:
        cache.delete_many([f"approval_flow_pending_count_{pk}" for pk in pks])


def _enter_node(instance, node) -> bool:
    """进入节点：解析候选并建 PENDING 任务 + 通知；无候选返回 False（调用方跳过该节点）。

    无候选（如部门 leader 被清空）在推进期发生时不阻塞流程：写一行 assignee 为空的
    审计任务（comment 注明自动通过），保证行为可追溯。委托代审的候选会记录
    delegate_from（原审批人），供审批轨迹标注「由 X 代理」。
    """
    ApprovalNodeTask = _models().Task
    now = timezone.now()
    pairs = resolve_assignee_pairs(node, instance.creator, instance.form_data)
    if not pairs:
        ApprovalNodeTask.objects.create(
            instance=instance,
            node=node,
            node_name=node.name,
            node_order=node.order,
            assignee=None,
            actor=None,
            status=ApprovalNodeTask.Status.APPROVED,
            comment=str(_("No available approver, node auto-approved")),
            acted_at=now,
        )
        logger.warning("approval flow node auto-approved (no candidate). instance:%s node:%s", instance.pk, node.pk)
        return False

    candidates = [user for user, _source in pairs]
    tasks = [
        ApprovalNodeTask.objects.create(
            instance=instance,
            node=node,
            node_name=node.name,
            node_order=node.order,
            assignee=user,
            delegate_from=source,
        )
        for user, source in pairs
    ]
    _notify(candidates, "submitted", instance)
    _invalidate_pending_count(candidates)
    return bool(tasks)


def create_instance(*, flow, applicant, title, form_data, biz_type="", biz_id=""):
    """发起申请：校验表单与全部可达节点候选，建实例并进入首节点。

    返回 (instance, error)：error 为 None 表示成功。候选校验 fail-closed——
    任一可达节点无人可审即拒绝发起（避免在途中卡死或静默放行）。

    biz_type/biz_id：业务模块挂钩点——传入后实例与业务行绑定，
    终态时经 ``approval_instance_finished`` 信号回写业务状态；留空 = 引擎自带
    表单的独立申请（历史行为不变）。
    """
    ApprovalInstance = _models().Instance

    if not flow.is_active:
        return None, str(_("The flow is disabled"))
    error = validate_form(flow, form_data)
    if error:
        return None, error
    path = simulate_path(flow, form_data)
    if path is None:
        return None, str(_("The flow routes contain a loop, please contact the administrator"))
    if not path:
        return None, str(_("The flow has no available node"))
    for node in path:
        if not resolve_assignees(node, applicant, form_data):
            return None, str(_("No available approver for node {}").format(node.name))

    instance = ApprovalInstance.objects.create(
        flow=flow,
        flow_name=flow.name,
        title=(title or "").strip()[:128],
        form_data=form_data or {},
        creator=applicant,
        current_node=path[0],
        flow_version=flow.version,
        biz_type=(biz_type or "")[:64],
        biz_id=str(biz_id or "")[:64],
    )
    _enter_node(instance, path[0])
    _emit_flow_event("flow.submitted", instance)
    return instance, None


def _emit_flow_event(event: str, instance) -> None:
    """出站 Webhook：流程实例事件（emit 全程吞异常，不影响审批流转）。

    payload 只含摘要字段，不含 form_data——表单内容可能敏感，订阅方需要明细时
    用自身凭证走 API 按流程取（与轻量审批 _emit_approval_event 同口径）。
    """
    from system.utils.webhook import emit_webhook_event

    try:
        emit_webhook_event(
            event,
            {
                "instance_no": str(instance.pk)[:8].upper(),
                "title": instance.title,
                "flow_name": instance.flow_name,
                "status": instance.status,
                "creator": getattr(instance.creator, "username", ""),
                "current_node": getattr(instance.current_node, "name", "") or "",
                "reason": instance.reason or "",
            },
        )
    except Exception:  # noqa: BLE001 双保险（emit 自身已吞异常）
        logger.warning("emit flow webhook failed: %s", event, exc_info=True)


def _finish_instance(instance, status, reason=None):
    from system.models.approval import ApprovalInstance

    ApprovalInstance.objects.filter(pk=instance.pk).update(
        status=status,
        current_node=None,
        reason=(reason or "")[:255],
        finished_at=timezone.now(),
        updated_time=timezone.now(),
    )
    instance.status = status
    instance.reason = reason
    instance.current_node = None
    event = _FLOW_FINISH_EVENTS.get(str(status))
    if event:
        _emit_flow_event(event, instance)
    _notify_business_finished(instance, status, reason)


def _notify_business_finished(instance, status, reason=None) -> None:
    """业务回调：实例到达终态时通知绑定的业务模块回写状态。

    仅在 biz_type 非空时发送；接收方在 system/signal_handler.py 注册，异常只记
    日志——业务回写失败不应影响审批主链路（与通知/Webhook 同口径）。
    """
    if not getattr(instance, "biz_type", ""):
        return
    from system.signal import approval_instance_finished

    try:
        approval_instance_finished.send(
            sender=type(instance), instance=instance, status=str(status), reason=reason or ""
        )
    except Exception:  # noqa: BLE001 业务回写故障不影响审批状态机
        logger.warning(
            "approval flow business callback failed. instance:%s biz:%s", instance.pk, instance.biz_type, exc_info=True
        )


def _cancel_pending_tasks(instance, node=None):
    ApprovalNodeTask = _models().Task

    queryset = ApprovalNodeTask.objects.filter(instance=instance, status=ApprovalNodeTask.Status.PENDING)
    if node is not None:
        queryset = queryset.filter(node=node)
    queryset.update(status=ApprovalNodeTask.Status.CANCELLED, updated_time=timezone.now())


def _advance(instance, node):
    """节点完成后推进：下一条件命中节点 / 实例通过。"""
    ApprovalInstance = _models().Instance

    while True:
        following = next_node(instance.flow, node.order, instance.form_data, node=node)
        if following is None:
            _finish_instance(instance, ApprovalInstance.Status.APPROVED)
            _invalidate_pending_count()
            _notify([instance.creator], "approved", instance)
            return
        entered = _enter_node(instance, following)
        if entered:
            ApprovalInstance.objects.filter(pk=instance.pk).update(current_node=following, updated_time=timezone.now())
            instance.current_node = following
            return
        # 无候选节点：审计行已写，继续找下一个节点（不改变 current_node）
        node = following


def _load_task(task_pk):
    ApprovalNodeTask = _models().Task

    return ApprovalNodeTask.objects.select_related("instance", "node", "assignee", "actor").filter(pk=task_pk).first()


def approve_task(task_pk, user, comment: str = ""):
    """通过当前待办任务：或签任一通过/会签全部通过后推进。返回 (ok, detail)。"""
    ApprovalInstance, ApprovalNodeTask = _models().Instance, _models().Task

    task = _load_task(task_pk)
    if task is None:
        return False, str(_("The task does not exist"))
    if task.status != ApprovalNodeTask.Status.PENDING:
        return False, str(_("The task has been processed"))
    instance = task.instance
    if instance.status != ApprovalInstance.Status.PENDING:
        return False, str(_("The application has been finished"))
    if task.assignee_id != user.pk:
        return False, str(_("This task is not assigned to you"))
    if instance.creator_id == user.pk:
        return False, str(_("The applicant cannot approve their own application"))
    if task.node_id and instance.current_node_id != task.node_id:
        return False, str(_("The task is not in the current node"))
    if task.node is None:
        # 节点被删除（有 PENDING 实例的流程禁止改动节点，理论不可达；fail-closed 兜底）
        return False, str(_("The node has been removed, please contact the administrator"))

    now = timezone.now()
    updated = ApprovalNodeTask.objects.filter(pk=task.pk, status=ApprovalNodeTask.Status.PENDING).update(
        status=ApprovalNodeTask.Status.APPROVED,
        actor=user,
        comment=(comment or "")[:255],
        acted_at=now,
        updated_time=now,
    )
    if not updated:
        return False, str(_("The task has been processed"))

    node = task.node
    instance.refresh_from_db()
    _invalidate_pending_count()
    if node.approve_type == node.ApproveType.OR:
        # 或签：任一通过即节点通过，其余待办作废
        _cancel_pending_tasks(instance, node=node)
        _advance(instance, node)
    elif node.approve_type == node.ApproveType.RATIO:
        # 比例会签：通过数/候选总数 ≥ ratio% 即通过；
        # 剩余可决人数不足以达标时提前驳回（全员拒绝必然落入此条件）
        node_tasks = ApprovalNodeTask.objects.filter(instance=instance, node=node)
        total = node_tasks.count()
        approved = node_tasks.filter(status=ApprovalNodeTask.Status.APPROVED).count()
        pending = node_tasks.filter(status=ApprovalNodeTask.Status.PENDING).count()
        required = -(-total * (node.approve_ratio or 100) // 100)  # ceil
        if approved >= required:
            _cancel_pending_tasks(instance, node=node)
            _advance(instance, node)
        elif approved + pending < required:
            _cancel_pending_tasks(instance)
            _finish_instance(
                instance,
                ApprovalInstance.Status.REJECTED,
                str(_("Approval ratio cannot be reached, the application is rejected")),
            )
            _notify([instance.creator], "rejected", instance)
        # 其余：等待更多审批人处理
    else:
        remaining = ApprovalNodeTask.objects.filter(
            instance=instance, node=node, status=ApprovalNodeTask.Status.PENDING
        ).exists()
        if not remaining:
            _advance(instance, node)
    return True, None


def reject_task(task_pk, user, reason: str):
    """驳回：任务置 REJECTED、实例驳回（终态）、其余待办作废、通知申请人。返回 (ok, detail)。"""
    ApprovalInstance, ApprovalNodeTask = _models().Instance, _models().Task

    reason = (reason or "").strip()
    if not reason:
        return False, str(_("Rejection reason is required"))

    task = _load_task(task_pk)
    if task is None:
        return False, str(_("The task does not exist"))
    if task.status != ApprovalNodeTask.Status.PENDING:
        return False, str(_("The task has been processed"))
    instance = task.instance
    if instance.status != ApprovalInstance.Status.PENDING:
        return False, str(_("The application has been finished"))
    if task.assignee_id != user.pk:
        return False, str(_("This task is not assigned to you"))
    if instance.creator_id == user.pk:
        return False, str(_("The applicant cannot approve their own application"))
    if task.node is None:
        return False, str(_("The node has been removed, please contact the administrator"))

    now = timezone.now()
    updated = ApprovalNodeTask.objects.filter(pk=task.pk, status=ApprovalNodeTask.Status.PENDING).update(
        status=ApprovalNodeTask.Status.REJECTED, actor=user, comment=reason[:255], acted_at=now, updated_time=now
    )
    if not updated:
        return False, str(_("The task has been processed"))

    _cancel_pending_tasks(instance)
    _finish_instance(instance, ApprovalInstance.Status.REJECTED, reason=reason)
    _invalidate_pending_count()
    _notify([instance.creator], "rejected", instance, extra=reason)
    return True, None


def cancel_instance(instance, user):
    """撤回：仅申请人、仅 PENDING；待办作废并通知当前节点审批人。返回 (ok, detail)。"""
    ApprovalInstance, ApprovalNodeTask = _models().Instance, _models().Task

    if instance.creator_id != user.pk:
        return False, str(_("Only the applicant can cancel the application"))
    if instance.status != ApprovalInstance.Status.PENDING:
        return False, str(_("Only pending applications can be cancelled"))

    pending = list(
        ApprovalNodeTask.objects.filter(instance=instance, status=ApprovalNodeTask.Status.PENDING)
        .select_related("assignee")
        .exclude(assignee=None)
    )
    _cancel_pending_tasks(instance)
    _finish_instance(instance, ApprovalInstance.Status.CANCELLED)
    _invalidate_pending_count()
    _notify([task.assignee for task in pending], "cancelled", instance)
    return True, None


def urge_instance(instance, user, message: str = ""):
    """人工催办：申请人（或超管）提醒当前节点审批人尽快处理。返回 (ok, detail)。

    - 仅申请人本人（或超管）、仅 PENDING 实例；
    - 通知对象 = 当前节点的待办任务处理人；无可催对象时拒绝（不占用节流窗口）；
    - 节流：同一实例 URGE_THROTTLE_SECONDS 内只发一次（防刷通知，缓存键随实例）。
    """
    from django.core.cache import cache

    ApprovalInstance, ApprovalNodeTask = _models().Instance, _models().Task

    if instance.creator_id != user.pk and not getattr(user, "is_superuser", False):
        return False, str(_("Only the applicant can urge the application"))
    if instance.status != ApprovalInstance.Status.PENDING:
        return False, str(_("Only pending applications can be urged"))

    cache_key = f"approval_flow_urge_{instance.pk}"
    if not cache.add(cache_key, 1, URGE_THROTTLE_SECONDS):
        return False, str(_("Please do not urge repeatedly within {} minutes").format(URGE_THROTTLE_SECONDS // 60))

    pending = (
        ApprovalNodeTask.objects.filter(instance=instance, status=ApprovalNodeTask.Status.PENDING)
        .select_related("assignee")
        .exclude(assignee=None)
    )
    targets = {task.assignee for task in pending}
    if not targets:
        cache.delete(cache_key)
        return False, str(_("There is no pending approver to urge"))

    _notify(targets, "urge", instance, extra=(message or "").strip()[:200])
    return True, None


def add_sign(instance, user, usernames, comment: str = ""):
    """加签：在当前节点追加候选审批人（会签语义下新候选必须通过）。返回 (ok, detail)。

    权限：当前节点任一任务的处理人/被指派人或超管；不能加签申请人本人。
    """
    ApprovalInstance, ApprovalNodeTask = _models().Instance, _models().Task
    UserInfo = _users()

    if instance.status != ApprovalInstance.Status.PENDING:
        return False, str(_("Only pending applications can be counter-signed"))
    if instance.current_node_id is None:
        return False, str(_("The application has no active node"))

    node = instance.current_node
    is_participant = (
        ApprovalNodeTask.objects.filter(instance=instance, node=node).filter(Q(assignee=user) | Q(actor=user)).exists()
    )
    if not (is_participant or user.is_superuser):
        return False, str(_("Only the current node approvers can counter-sign"))

    names = _split_values(usernames)
    if not names:
        return False, str(_("Please select the approver to add"))
    candidates = list(UserInfo.objects.filter(is_active=True, username__in=names).exclude(pk=instance.creator_id))
    if not candidates:
        return False, str(_("No available approver for counter-sign"))

    existing = set(ApprovalNodeTask.objects.filter(instance=instance, node=node).values_list("assignee_id", flat=True))
    added = []
    for candidate in candidates:
        if candidate.pk in existing:
            continue
        ApprovalNodeTask.objects.create(
            instance=instance,
            node=node,
            node_name=node.name,
            node_order=node.order,
            assignee=candidate,
            is_added=True,
            comment=(comment or "")[:255],
        )
        added.append(candidate)
    if not added:
        return False, str(_("The selected approver is already in the node"))

    _notify(added, "added", instance, extra=comment)
    _invalidate_pending_count(added)
    return True, None
