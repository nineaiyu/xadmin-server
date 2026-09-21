#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批流的辅助动作：人工催办（urge）、加签（add_sign）与转交（transfer_task）。

职责边界：**不改变节点推进规则**的参与者侧动作——催办只发提醒、加签只给当前节点
追加候选、转交只替换当前待办的处理人（后续推进仍由 engine 的 approve/reject 驱动）。
与 engine 单向依赖：本模块复用其通知/待办计数失效/任务加载；engine 不反向依赖
本模块（包级 ``__init__`` 统一再导出）。拆分的另一动因是文件行数门禁。
"""

from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from . import engine
from .conditions import _split_values
from .constants import _models, _users

# 人工催办节流窗口（秒）：同一实例内申请人在窗口内重复催办只发一次通知
URGE_THROTTLE_SECONDS = 600


def urge_instance(instance, user, message: str = ""):
    """人工催办：申请人（或超管）提醒当前节点审批人尽快处理。返回 (ok, detail)。

    - 仅申请人本人（或超管）、仅 PENDING 实例；
    - 通知对象 = 当前节点的待办任务处理人；无可催对象时拒绝（不占用节流窗口）；
    - 节流：同一实例 URGE_THROTTLE_SECONDS 内只发一次（防刷通知，缓存键随实例）。
    """
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

    engine._notify(targets, "urge", instance, extra=(message or "").strip()[:200])
    return True, None


def add_sign(instance, user, usernames, comment: str = ""):
    """加签：在当前节点追加候选审批人（会签语义下新候选必须通过）。返回 (ok, detail)。

    权限：当前节点任一任务的处理人/被指派人或超管；不能加签申请人本人。
    并发安全：实例行锁使加签与同时发生的审批推进串行化（不会加到已被推进/终结的节点）。
    """
    ApprovalInstance, ApprovalNodeTask = _models().Instance, _models().Task
    UserInfo = _users()

    names = _split_values(usernames)
    if not names:
        return False, str(_("Please select the approver to add"))

    with transaction.atomic():
        # 锁实例行并在锁内重新读取（状态与当前节点以锁内版本为准）
        instance = ApprovalInstance.objects.select_for_update().filter(pk=instance.pk).first()
        if instance is None:
            return False, str(_("The application does not exist"))
        if instance.status != ApprovalInstance.Status.PENDING:
            return False, str(_("Only pending applications can be counter-signed"))
        if instance.current_node_id is None:
            return False, str(_("The application has no active node"))

        node = instance.current_node
        if node.approve_type == node.ApproveType.OR:
            # 或签节点下任一审批人通过即流转，加签对「谁能推动节点」没有约束力
            # （新候选也会随首个通过被作废），语义弱化到没有意义 → 明确拒绝并给出替代路径
            return False, str(
                _(
                    "The current node is an OR-sign node: any approver can move it forward, "
                    "so counter-signing has no effect. Please use transfer instead"
                )
            )
        is_participant = (
            ApprovalNodeTask.objects.filter(instance=instance, node=node)
            .filter(Q(assignee=user) | Q(actor=user))
            .exists()
        )
        if not (is_participant or user.is_superuser):
            return False, str(_("Only the current node approvers can counter-sign"))

        candidates = list(UserInfo.objects.filter(is_active=True, username__in=names).exclude(pk=instance.creator_id))
        if not candidates:
            return False, str(_("No available approver for counter-sign"))

        existing = set(
            ApprovalNodeTask.objects.filter(instance=instance, node=node).values_list("assignee_id", flat=True)
        )
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

        engine._notify(added, "added", instance, extra=comment)
        engine._invalidate_pending_count(added)
        return True, None


def transfer_task(task_pk, user, to_username: str, comment: str = ""):
    """转交：把当前待办转给另一名用户处理（一次性，区别于长期「委托」）。返回 (ok, detail)。

    - 权限：任务处理人本人或超管；仅 PENDING 任务 / PENDING 实例 / 当前节点；
    - 目标：启用用户，且非申请人本人（申请人不能审批自己的申请）、非当前处理人；
    - 审计：原任务置 CANCELLED（comment 注明转交给谁，保留在流转时间线），
      新任务 `delegate_from` = 原处理人（时间线据此标注「由 X 代理」）+ comment 记转交说明；
    - 通知：新处理人（transferred）；两人待办计数缓存失效。
    """
    ApprovalInstance, ApprovalNodeTask = _models().Instance, _models().Task
    UserInfo = _users()

    username = (to_username or "").strip()
    if not username:
        return False, str(_("Please select the user to transfer to"))

    with transaction.atomic():
        task = engine._load_task(task_pk)
        if task is None:
            return False, str(_("The task does not exist"))
        instance = ApprovalInstance.objects.select_for_update().filter(pk=task.instance_id).first()
        if instance is None:
            return False, str(_("The application does not exist"))
        if task.status != ApprovalNodeTask.Status.PENDING:
            return False, str(_("The task has been processed"))
        if instance.status != ApprovalInstance.Status.PENDING:
            return False, str(_("The application has been finished"))
        if task.assignee_id != user.pk and not getattr(user, "is_superuser", False):
            return False, str(_("This task is not assigned to you"))
        if task.node_id and instance.current_node_id != task.node_id:
            return False, str(_("The task is not in the current node"))

        target = UserInfo.objects.filter(is_active=True, username=username).first()
        if target is None:
            return False, str(_("The target user does not exist or is disabled"))
        if target.pk == instance.creator_id:
            return False, str(_("The applicant cannot approve their own application"))
        if target.pk == task.assignee_id:
            return False, str(_("The task is already assigned to this user"))

        now = timezone.now()
        note = (comment or "").strip()[:200]
        # CAS 作废原任务：并发下同一待办只能被转交/处理一次
        transferred_note = str(_("Transferred to {}")).format(target.nickname or target.username)
        updated = ApprovalNodeTask.objects.filter(pk=task.pk, status=ApprovalNodeTask.Status.PENDING).update(
            status=ApprovalNodeTask.Status.CANCELLED,
            comment=transferred_note[:255],
            updated_time=now,
        )
        if not updated:
            return False, str(_("The task has been processed"))

        ApprovalNodeTask.objects.create(
            instance=instance,
            node=task.node,
            node_name=task.node_name,
            node_order=task.node_order,
            assignee=target,
            # 复用「原处理人」字段：时间线据此标注来源；该任务非加签所得（is_added=False）
            delegate_from=task.assignee,
            comment=note[:255],
        )
        engine._notify([target], "transferred", instance, extra=note)
        previous = [task.assignee] if task.assignee_id else []
        engine._invalidate_pending_count([target, *previous])
        return True, None
