#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全量审批流引擎：实例推进（发起 / 通过 / 驳回 / 撤回 / 加签）。"""

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
    ordered_nodes,
    resolve_assignee_pairs,
    resolve_assignees,
    simulate_path,
    validate_form,
)
from .constants import _models
from .engine_events import (  # noqa: F401 再导出：事件/通知调用面保持不变
    _alert_auto_approved,
    _cancel_pending_tasks,
    _emit_flow_event,
    _finish_instance,
    _invalidate_pending_count,
    _notify,
    _notify_business_finished,
)

logger = get_logger(__name__)


def _enter_node(instance, node) -> bool:
    """进入节点：解析候选并建 PENDING 任务 + 通知；无候选返回 False（调用方跳过该节点）。

    无候选（如部门 leader 被清空）在推进期发生时不阻塞流程：写一行 assignee 为空的
    审计任务（comment 注明自动通过），保证行为可追溯。委托代审的候选会记录
    delegate_from（原审批人），供审批轨迹标注「由 X 代理」。

    任务行批量创建（候选数上限内单次 INSERT）；候选解析、通知与待办计数失效语义不变。
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
        _alert_auto_approved(instance, node)
        return False

    candidates = [user for user, _source in pairs]
    ApprovalNodeTask.objects.bulk_create(
        [
            ApprovalNodeTask(
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
    )
    _notify(candidates, "submitted", instance)
    _invalidate_pending_count(candidates)
    return True


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


def _advance(instance, node):
    """节点完成后推进：下一条件命中节点 / 实例通过。

    并发安全：调用方（approve_task 等）持有实例行锁（select_for_update）时同一实例
    的推进会串行化；终态跃迁另有 CAS 兜底（见 _finish_instance）。

    节点集一次取数后整段复用（连续跳过无候选节点时不再逐步查库）。
    """
    ApprovalInstance = _models().Instance

    version = _instance_version(instance)
    nodes = ordered_nodes(instance.flow, version)
    while True:
        following = next_node(instance.flow, node.order, instance.form_data, node=node, version=version, nodes=nodes)
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


def _settle_node_after_approve(instance, node) -> set:
    """节点通过后的结算（OR/RATIO/AND 共用）：返回需要失效待办计数的 assignee pk 集合。

    人工通过（approve_task）与超时自动通过（periodic.execute_timeout_actions）共用：
    - 或签：节点即通过，其余待办作废并推进；
    - 比例会签：达标即推进；剩余可决人数不足以达标时提前驳回整单；
    - 会签：无剩余待办才推进。
    调用方持有实例行锁；调用前本任务已完成（或签外不自动作废他人）。
    """
    ApprovalNodeTask = _models().Task
    ApprovalInstance = _models().Instance

    invalidated: set = set()
    if node.approve_type == node.ApproveType.OR:
        # 或签：任一通过即节点通过，其余待办作废
        invalidated.update(_cancel_pending_tasks(instance, node=node))
        _advance(instance, node)
    elif node.approve_type == node.ApproveType.RATIO:
        # 比例会签：通过数/候选总数 ≥ ratio% 即通过；已作废行（转交换人/减签移除）
        # 不计入候选总数——减签才能真正降低达标线（转交一出一进、总数不变）；
        # 剩余可决人数不足以达标时提前驳回（全员拒绝必然落入此条件）
        node_tasks = ApprovalNodeTask.objects.filter(instance=instance, node=node).exclude(
            status=ApprovalNodeTask.Status.CANCELLED
        )
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
    return invalidated


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
        invalidated.update(_settle_node_after_approve(instance, node))
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
