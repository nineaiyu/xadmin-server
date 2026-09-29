#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全量审批流引擎：实例推进（发起 / 通过 / 驳回 / 撤回 / 加签）。"""

from functools import partial

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from approval.utils.approval.display import user_display
from common.utils import get_logger

from .assignees import (
    _no_approver_detail as _no_approver_detail,  # noqa: PLC0414 显式再导出（engine 调用面与外部引用保持）
)
from .assignees import (
    _resolve_instance_cc as _resolve_instance_cc,
)
from .conditions import (
    next_node,
    resolve_assignee_pairs,
    resolve_assignees,
    simulate_path,
    validate_form,
)
from .constants import _FLOW_FINISH_EVENTS, _models

logger = get_logger(__name__)


def _notify(users, event, instance, extra=None):
    """向用户列表推送流程通知（单条失败只记日志，不阻断推进）。

    入队延迟到事务提交后（`transaction.on_commit`）：发起/通过/驳回/撤回/加签都在
    `transaction.atomic()` 内推进实例，提交前入队一旦事务回滚，就会留下指向不存在
    实例的通知（点进去 404）。无活动事务时 Django 立即执行回调，语义与改造前一致。
    """
    from system.notifications import ApprovalFlowMessage

    def _send(user):
        try:
            ApprovalFlowMessage(user, event, instance, extra=extra).publish(is_async=True)
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
            # 处理人显示名快照（用户删除/改名后轨迹仍可读）
            assignee_display=user_display(user),
            delegate_from=source,
        )
        for user, source in pairs
    ]
    _notify(candidates, "submitted", instance)
    _invalidate_pending_count(candidates)
    return bool(tasks)


def _instance_version(instance):
    """实例钉住的定义版本：推进按该版本取节点集；空/0 回退当前生效定义。

    改造前的老实例理论上都有 flow_version（发起时写入），这里兜底手工造的历史行。
    """
    version = getattr(instance, "flow_version", None)
    return version if version and version > 0 else None


def create_instance(*, flow, applicant, title, form_data, biz_type="", biz_id="", cc_users=None):
    """发起申请：校验表单与全部可达节点候选，建实例并进入首节点。

    返回 (instance, error)：error 为 None 表示成功。候选校验 fail-closed——
    任一可达节点无人可审即拒绝发起（避免在途中卡死或静默放行）。

    biz_type/biz_id：业务模块挂钩点——传入后实例与业务行绑定，
    终态时经 ``approval_instance_finished`` 信号回写业务状态；留空 = 引擎自带
    表单的独立申请（历史行为不变）。

    cc_users：发起时追加的抄送人（用户 pk 列表）；实例抄送人 = 可达节点
    ``cc_users`` 并集 + 本参数，落实例快照并即时知会（终态再次知会）。

    事务边界：发起链路整体在一个事务内（校验失败提前返回，不产生写入），中途异常
    整体回滚——不会留下「PENDING 但无任何节点任务」的卡死单；业务接入方无需自行
    包事务（嵌套 atomic 即 savepoint，无害）。

    版本化：流程行加锁后再模拟路径，保证「路径所求的节点集」与「实例钉住的
    flow_version」一致（与并发改版串行化）；实例按钉住版本推进，改版不影响在途单。
    """
    ApprovalFlow, ApprovalInstance = _models().Flow, _models().Instance

    with transaction.atomic():
        flow = ApprovalFlow.objects.select_for_update().filter(pk=flow.pk).first()
        if flow is None:
            return None, str(_("The flow does not exist"))
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
                return None, _no_approver_detail(node, applicant)

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
        cc_list = _resolve_instance_cc(path, applicant, cc_users)
        if cc_list:
            instance.cc_users.set(cc_list)
            _notify(cc_list, "cc", instance)
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


def _advance(instance, node):
    """节点完成后推进：下一条件命中节点 / 实例通过。

    并发安全：调用方（approve_task 等）持有实例行锁（select_for_update）时同一实例
    的推进会串行化；终态跃迁另有 CAS 兜底（见 _finish_instance）。
    """
    ApprovalInstance = _models().Instance

    while True:
        following = next_node(
            instance.flow, node.order, instance.form_data, node=node, version=_instance_version(instance)
        )
        if following is None:
            # 终态通过：当前节点任务已由本次动作处理完毕（含或签/比例会签的作废，
            # 失效集在调用点给出），实例到达终态后不存在遗留 PENDING 任务——
            # 此处无需再失效任何人的待办计数。
            if _finish_instance(instance, ApprovalInstance.Status.APPROVED):
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
    """通过当前待办任务：或签任一通过/会签全部通过后推进。返回 (ok, detail)。

    并发安全：实例行锁（select_for_update）使同一实例的并发审批串行化——两个
    审批人几乎同时通过时不会重复推进（下一节点重复建任务组）。sqlite（测试
    环境）忽略行锁，此时退化到「任务级 CAS + 终态 CAS」兜底。
    """
    ApprovalInstance, ApprovalNodeTask = _models().Instance, _models().Task

    with transaction.atomic():
        task = _load_task(task_pk)
        if task is None:
            return False, str(_("The task does not exist"))
        if task.status != ApprovalNodeTask.Status.PENDING:
            return False, str(_("The task has been processed"))
        # 锁实例行并在锁内重新读取（锁外拿到的是快照，可能已被并发请求推进）
        instance = ApprovalInstance.objects.select_for_update().filter(pk=task.instance_id).first()
        if instance is None:
            return False, str(_("The application does not exist"))
        if instance.status != ApprovalInstance.Status.PENDING:
            return False, str(_("The application has been finished"))
        if task.assignee_id != user.pk:
            return False, str(_("This task is not assigned to you"))
        if instance.creator_id == user.pk:
            return False, str(_("The applicant cannot approve their own application"))
        if task.node_id and instance.current_node_id != task.node_id:
            return False, str(_("The task is not in the current node"))
        if task.node is None:
            # 节点行缺失（改版只收口不删除，理论不可达；沿用 fail-closed 兜底历史数据）
            return False, str(_("The node has been removed, please contact the administrator"))

        now = timezone.now()
        updated = ApprovalNodeTask.objects.filter(pk=task.pk, status=ApprovalNodeTask.Status.PENDING).update(
            status=ApprovalNodeTask.Status.APPROVED,
            actor=user,
            actor_display=user_display(user),
            comment=(comment or "")[:255],
            acted_at=now,
            updated_time=now,
        )
        if not updated:
            return False, str(_("The task has been processed"))

        node = task.node
        # 精确失效集：处理人自己 + 被作废任务的 assignee（新节点候选人在 _enter_node 内失效）
        invalidated = {user.pk}
        if node.approve_type == node.ApproveType.OR:
            # 或签：任一通过即节点通过，其余待办作废
            invalidated.update(_cancel_pending_tasks(instance, node=node))
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
                invalidated.update(_cancel_pending_tasks(instance, node=node))
                _advance(instance, node)
            elif approved + pending < required:
                invalidated.update(_cancel_pending_tasks(instance))
                if _finish_instance(
                    instance,
                    ApprovalInstance.Status.REJECTED,
                    str(_("Approval ratio cannot be reached, the application is rejected")),
                ):
                    _notify([instance.creator], "rejected", instance)
            # 其余：等待更多审批人处理
        else:
            remaining = ApprovalNodeTask.objects.filter(
                instance=instance, node=node, status=ApprovalNodeTask.Status.PENDING
            ).exists()
            if not remaining:
                _advance(instance, node)
        _invalidate_pending_count(invalidated)
        return True, None


def reject_task(task_pk, user, reason: str):
    """驳回：任务置 REJECTED、实例驳回（终态）、其余待办作废、通知申请人。返回 (ok, detail)。"""
    reason = (reason or "").strip()
    if not reason:
        return False, str(_("Rejection reason is required"))

    with transaction.atomic():
        return _reject_task_locked(task_pk, user, reason)


def _reject_task_locked(task_pk, user, reason: str):
    """驳回主体（调用方已进入事务：实例行锁保证并发串行化）。"""
    ApprovalInstance, ApprovalNodeTask = _models().Instance, _models().Task

    task = _load_task(task_pk)
    if task is None:
        return False, str(_("The task does not exist"))
    if task.status != ApprovalNodeTask.Status.PENDING:
        return False, str(_("The task has been processed"))
    instance = ApprovalInstance.objects.select_for_update().filter(pk=task.instance_id).first()
    if instance is None:
        return False, str(_("The application does not exist"))
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
        status=ApprovalNodeTask.Status.REJECTED,
        actor=user,
        actor_display=user_display(user),
        comment=reason[:255],
        acted_at=now,
        updated_time=now,
    )
    if not updated:
        return False, str(_("The task has been processed"))

    # 精确失效集：处理人自己 + 被作废任务（整单全部 PENDING）的 assignee
    invalidated = {user.pk}
    invalidated.update(_cancel_pending_tasks(instance))
    if _finish_instance(instance, ApprovalInstance.Status.REJECTED, reason=reason):
        _notify([instance.creator], "rejected", instance, extra=reason)
    _invalidate_pending_count(invalidated)
    return True, None


def cancel_instance(instance, user):
    """撤回：申请人本人或超管、仅 PENDING；待办作废并通知当前节点审批人。返回 (ok, detail)。

    超管放行用于运营清障：演示/离职账号发起的在途单若无人可撤回，可请管理员代为
    撤回终止（流程改版自绑定版本起不再受在途单阻塞，此处只为尽早收敛脏单）。
    """
    with transaction.atomic():
        return _cancel_instance_locked(instance, user)


def _cancel_instance_locked(instance, user):
    """撤回主体（调用方已进入事务：实例行锁保证并发串行化）。"""
    ApprovalInstance, ApprovalNodeTask = _models().Instance, _models().Task

    # 锁实例行并在锁内重新读取：与并发的审批/驳回互斥
    instance = ApprovalInstance.objects.select_for_update().filter(pk=instance.pk).first()
    if instance is None:
        return False, str(_("The application does not exist"))
    if instance.creator_id != user.pk and not getattr(user, "is_superuser", False):
        return False, str(_("Only the applicant can cancel the application"))
    if instance.status != ApprovalInstance.Status.PENDING:
        return False, str(_("Only pending applications can be cancelled"))

    pending = list(
        ApprovalNodeTask.objects.filter(instance=instance, status=ApprovalNodeTask.Status.PENDING)
        .select_related("assignee")
        .exclude(assignee=None)
    )
    _cancel_pending_tasks(instance)
    if _finish_instance(instance, ApprovalInstance.Status.CANCELLED):
        _notify([task.assignee for task in pending], "cancelled", instance)
    # 精确失效集：被作废待办的全体 assignee（pending 已加载，无需二次查询）
    _invalidate_pending_count({task.assignee_id for task in pending})
    return True, None
