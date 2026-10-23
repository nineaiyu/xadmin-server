#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批流的退回动作：驳回即终止之外的「退回上一/指定节点重审」。

与 engine 的关系：退回**改变推进位置**（current_node 回退并重开目标节点），但复用
引擎的节点候选解析 / 任务落库 / 待办失效原语，自身不做推进（目标节点重新走完后再由
engine 的 approve → _advance 正常向前流转）。engine 不反向依赖本模块（包级
``__init__`` 统一再导出）。

版本化联动：目标节点行按实例钉住的 ``flow_version`` 解析
（``nodes_effective_at``），退回只重开「实例实际经过的那一行」，改版不影响在途单。
"""

from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from approval.utils.approval.display import user_display

from .conditions import nodes_effective_at, resolve_assignee_pairs
from .constants import _models
from .engine import (
    _cancel_pending_tasks,
    _emit_flow_event,
    _instance_version,
    _invalidate_pending_count,
    _notify,
)


def returnable_nodes(instance: Any) -> list[Any]:
    """可退回节点列表（实例已途经、非当前节点），按 order 降序（上一节点在前）。

    「已途经」= 存在任意状态的任务行（任务即节点被进入的事实记录）；返回
    ``[{"order", "name"}]`` 供退回弹窗选择。
    """
    ApprovalNodeTask = _models().Task
    if instance.status != _models().Instance.Status.PENDING or instance.current_node_id is None:
        return []

    visited = (
        ApprovalNodeTask.objects.filter(instance=instance)
        .exclude(node_order=instance.current_node.order)
        .order_by("node_order")
        .values_list("node_order", "node_name")
    )
    # 同一 order 可能有多行（或签/会签/历史往返）：按 order 去重保留最近名称
    by_order: dict[str, Any] = {}
    for order, name in visited:
        by_order[order] = name
    return [{"order": order, "name": by_order[order]} for order in sorted(by_order, reverse=True)]


@transaction.atomic  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def return_instance(instance: Any, user: Any, reason: str, target_order: Any = None, task_pk: Any = None) -> Any:
    """退回：当前节点待办作废，实例回退到已途经的目标节点重开重审。返回 (ok, detail)。

    - 权限：当前节点待办处理人本人或超管（引擎侧复核，视图侧另有可见域收敛）；
    - 目标：缺省 = 上一途经节点；显式指定必须**已途经**且 order 小于当前节点
      （不允许跳到未到达的节点，防止构造从未走过的路径）；
    - 目标节点无可用审批人时 fail-closed 拒绝（先解析候选、后落任何写入）；
    - 语义：实例保持 PENDING、current_node 回退，目标节点重新建 PENDING 任务；
      退回原因随被作废任务与通知留痕，Webhook 发 ``flow.returned``。
    """
    ApprovalInstance, ApprovalNodeTask = _models().Instance, _models().Task

    reason = (reason or "").strip()
    if not reason:
        return False, str(_("Return reason is required"))

    # 锁实例行并在锁内重新读取：与并发的审批/驳回/撤回互斥
    instance = ApprovalInstance.objects.select_for_update().filter(pk=instance.pk).first()
    if instance is None:
        return False, str(_("The application does not exist"))
    if instance.status != ApprovalInstance.Status.PENDING:
        return False, str(_("Only pending applications can be returned"))
    if instance.current_node_id is None or instance.current_node is None:
        return False, str(_("The application has no active node"))

    is_participant = (
        ApprovalNodeTask.objects.filter(instance=instance, node=instance.current_node)
        .filter(Q(assignee=user) | Q(actor=user))
        .exists()
    )
    if not (is_participant or getattr(user, "is_superuser", False)):
        return False, str(_("Only the current node approvers can return the application"))

    current_order = instance.current_node.order
    visited = set(ApprovalNodeTask.objects.filter(instance=instance).values_list("node_order", flat=True))
    if target_order in (None, ""):
        earlier = [order for order in visited if order < current_order]
        if not earlier:
            return False, str(_("There is no earlier node to return to"))
        target_order = max(earlier)
    try:
        target_order = int(target_order)
    except (TypeError, ValueError):
        return False, str(_("Operation failed. Abnormal data"))
    if target_order >= current_order:
        return False, str(_("The return target must be an earlier node that has been visited"))
    if target_order not in visited:
        return False, str(_("The return target must be an earlier node that has been visited"))

    # 版本化联动：解析实例钉住版本下的目标节点行（不存在即定义已不可达，fail-closed）
    target_node = nodes_effective_at(instance.flow, _instance_version(instance)).filter(order=target_order).first()
    if target_node is None:
        return False, str(_("The node has been removed, please contact the administrator"))

    # 目标节点候选预解析：无可用审批人时在写入前拒绝（退回不能造成卡死单）
    pairs = resolve_assignee_pairs(target_node, instance.creator, instance.form_data)
    if not pairs:
        return False, str(
            _("Node {} has no available approver. Please check the approver configuration of this node")
        ).format(target_node.name)

    now = timezone.now()
    return_note = str(_("Returned to node {}: {}")).format(target_node.name, reason[:180])
    # 处理人自己的任务带退回原因（其余当前节点待办由 _cancel_pending_tasks 统一作废）
    mine = (
        ApprovalNodeTask.objects.filter(instance=instance, node=instance.current_node)
        .filter(Q(assignee=user) | Q(actor=user))
        .first()
    )
    if mine is not None:
        ApprovalNodeTask.objects.filter(pk=mine.pk, status=ApprovalNodeTask.Status.PENDING).update(
            status=ApprovalNodeTask.Status.CANCELLED,
            actor=user,
            actor_display=user_display(user),
            comment=return_note[:255],
            acted_at=now,
            updated_time=now,
        )

    invalidated = set(_cancel_pending_tasks(instance))
    invalidated.add(user.pk)
    candidates = [user_ for user_, _source in pairs]
    for candidate in candidates:
        ApprovalNodeTask.objects.create(
            instance=instance,
            node=target_node,
            node_name=target_node.name,
            node_order=target_node.order,
            assignee=candidate,
            assignee_display=user_display(candidate),
        )
    ApprovalInstance.objects.filter(pk=instance.pk).update(current_node=target_node, updated_time=now)
    instance.current_node = target_node
    _notify(candidates, "returned", instance, extra=reason[:200])
    _notify([instance.creator], "returned", instance, extra=reason[:200])
    _invalidate_pending_count(invalidated | {candidate.pk for candidate in candidates})
    _emit_flow_event("flow.returned", instance)
    return True, None
