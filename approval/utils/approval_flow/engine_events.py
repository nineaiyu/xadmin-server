#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批流引擎的事件与通知（自 engine.py 平移）：通知投递、待办计数失效、出站 Webhook、
业务回调与作废待办。

对外的既有导入面（approval.utils.approval_flow.engine）经该模块再导出保持不变。
"""

from functools import partial

from django.db import transaction
from django.utils import timezone

from common.utils import get_logger

from .constants import _FLOW_FINISH_EVENTS, MAX_AUTO_APPROVE_NOTIFY_ADMINS, _models, _users

logger = get_logger(__name__)


def _notify(users, event, instance, extra=None, node_name=None):
    """向用户列表推送流程通知（单条失败只记日志，不阻断推进）。

    入队延迟到事务提交后（`transaction.on_commit`）：发起/通过/驳回/撤回/加签都在
    `transaction.atomic()` 内推进实例，提交前入队一旦事务回滚，就会留下指向不存在
    实例的通知（点进去 404）。无活动事务时 Django 立即执行回调，语义与改造前一致。

    ``node_name`` 为显式节点名：事件发生在「节点尚未成为 current_node」时（如无候选
    自动通过），由调用方直接给出，避免通知里显示上一个节点。
    """
    from approval.notifications import ApprovalFlowMessage

    def _send(user):
        try:
            ApprovalFlowMessage(user, event, instance, extra=extra, node_name=node_name).publish(is_async=True)
        except Exception:  # noqa: BLE001 通知链路故障不影响审批主流程
            logger.warning("send approval flow notify failed. instance:%s user:%s", instance.pk, user.pk, exc_info=True)

    for user in users:
        if not user:
            continue
        transaction.on_commit(partial(_send, user))


def _invalidate_pending_count(users):
    """失效待办计数缓存（精确集合：处理人 / 被作废任务 assignee / 新节点候选等）。

    历史实现无参时全量清「所有活跃用户」（UserInfo 全表扫描 + 最多 5000 键的
    delete_many），每个审批动作都触发一次；改为精确集后每个调用点只需给出本次
    动作实际影响的用户（接受 UserInfo 对象或 pk），无关用户不再被清。
    计数缓存 TTL 仅 10s：即便个别路径漏清也会自愈，不构成一致性问题。
    """
    from django.core.cache import cache

    pks = {user.pk if hasattr(user, "pk") else user for user in users}
    pks.discard(None)
    if pks:
        cache.delete_many([f"approval_flow_pending_count_{pk}" for pk in pks])


def _alert_auto_approved(instance, node) -> None:
    """节点无候选自动通过的治理告警：出站 Webhook + 知会启用中的超管。

    自动通过 = 流程按「无人可审」静默放行，属配置缺口，必须能被运维察觉
    （原来只有服务端日志与审计行，无任何外发信号）；超管知会同提交/终态事件走
    同一通知链路，单条失败只记日志。
    """
    _emit_flow_event(
        "flow.node_auto_approved",
        instance,
        extra={"node_name": node.name, "current_node": node.name},
    )
    UserInfo = _users()
    admins = list(UserInfo.objects.filter(is_superuser=True, is_active=True)[:MAX_AUTO_APPROVE_NOTIFY_ADMINS])
    if admins:
        _notify(admins, "auto_approved", instance, node_name=node.name)


def _emit_flow_event(event: str, instance, extra=None) -> None:
    """出站 Webhook：流程实例事件（emit 全程吞异常，不影响审批流转）。

    payload 只含摘要字段，不含 form_data——表单内容可能敏感，订阅方需要明细时
    用自身凭证走 API 按流程取（与轻量审批 _emit_approval_event 同口径）。
    ``extra`` 可覆盖/补充摘要字段（如自动通过事件的 node_name 与 current_node）。
    """
    from task.services import emit_webhook_event

    data = {
        "instance_no": str(instance.pk)[:8].upper(),
        "title": instance.title,
        "flow_name": instance.flow_name,
        "status": instance.status,
        "creator": getattr(instance.creator, "username", ""),
        "current_node": getattr(instance.current_node, "name", "") or "",
        "reason": instance.reason or "",
    }
    if extra:
        data.update(extra)
    try:
        emit_webhook_event(event, data)
    except Exception:  # noqa: BLE001 双保险（emit 自身已吞异常）
        logger.warning("emit flow webhook failed: %s", event, exc_info=True)


def _finish_instance(instance, status, reason=None) -> bool:
    """实例置终态（仅 PENDING → 终态，CAS）。返回 False = 已非 PENDING（被并发处理）。

    并发下同一实例可能被多个推进者同时尝试置终态（如两个审批人几乎同时通过最后
    节点）；CAS 保证只有首个跃迁生效——Webhook 出站、业务回调、申请人通知都只发生
    一次，不会重复投递。
    """
    from approval.models.approval import ApprovalInstance

    now = timezone.now()
    updated = ApprovalInstance.objects.filter(pk=instance.pk, status=ApprovalInstance.Status.PENDING).update(
        status=status,
        current_node=None,
        reason=(reason or "")[:255],
        finished_at=now,
        updated_time=now,
    )
    if not updated:
        return False
    instance.status = status
    instance.reason = reason
    instance.current_node = None
    event = _FLOW_FINISH_EVENTS.get(str(status))
    if event:
        _emit_flow_event(event, instance)
        # 抄送人终态知会（事件取终态对应文案：approved / rejected / cancelled）
        cc_list = [user for user in instance.cc_users.all() if user.is_active]
        if cc_list:
            _notify(cc_list, "cc", instance, extra={"status": str(status)})
    _notify_business_finished(instance, status, reason)
    return True


def _notify_business_finished(instance, status, reason=None) -> None:
    """业务回调：实例到达终态时通知绑定的业务模块回写状态。

    仅在 biz_type 非空时发送；接收方在 approval/signal_handler.py，同步器经
    approval/biz_sync.py 注册表解析，异常只记日志——业务回写失败不应影响审批
    主链路（与通知/Webhook 同口径）。
    """
    if not getattr(instance, "biz_type", ""):
        return
    from approval.signal import approval_instance_finished

    try:
        approval_instance_finished.send(
            sender=type(instance), instance=instance, status=str(status), reason=reason or ""
        )
    except Exception:  # noqa: BLE001 业务回写故障不影响审批状态机
        logger.warning(
            "approval flow business callback failed. instance:%s biz:%s", instance.pk, instance.biz_type, exc_info=True
        )


def _cancel_pending_tasks(instance, node=None) -> list:
    """作废待办任务（整单或指定节点）；返回被作废任务的 assignee pk 列表。

    返回值为待办计数缓存的精确失效集（先取值再 UPDATE；assignee 为空的任务
    本就计入不了任何人的待办）。
    """
    ApprovalNodeTask = _models().Task

    queryset = ApprovalNodeTask.objects.filter(instance=instance, status=ApprovalNodeTask.Status.PENDING)
    if node is not None:
        queryset = queryset.filter(node=node)
    assignee_pks = list(queryset.exclude(assignee=None).values_list("assignee", flat=True))
    queryset.update(status=ApprovalNodeTask.Status.CANCELLED, updated_time=timezone.now())
    return assignee_pks
