#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全量审批流引擎一期：流程实例推进（ADR-012）。

模型关系：ApprovalFlow（定义）→ ApprovalFlowNode（顺序节点，节点级条件）→
ApprovalInstance（一次申请）→ ApprovalNodeTask（一行一个候选审批人）。

与轻量敏感操作审批（system/utils/approval.py 的一次性令牌）完全独立：
- 令牌审批面向「拦截业务请求 → 批准后重发」，无表单、无多级；
- 本引擎面向业务表单（请假/报销类），无请求重放，状态机完整。

关键语义（ADR-012）：
- 条件分支：节点 condition 为真才经过该节点（不支持节点内多分支）；
- 或签 OR：任一 APPROVED 即节点通过，其余 PENDING 行置 CANCELLED；
- 会签 AND：全部 APPROVED 才通过；任一行 REJECTED → 实例驳回（终态）；
- 申请人不能审批自己的节点（候选解析时剔除申请人；无候选在发起时即报错）；
- 驳回/撤回均终止实例；加签在「当前节点」追加候选（会签语义下必须通过）。

并发说明：任务处理用「条件更新 + rowcount」原子占位（同轻量审批令牌消费口径，
sqlite 单测/E2E 库不支持 SELECT ... FOR UPDATE）。
"""

import datetime
from types import SimpleNamespace

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)

# 待办计数短缓存（秒）：顶栏角标/页签角标高频轮询（与轻量审批 10s 口径一致）
FLOW_PENDING_COUNT_CACHE_SECONDS = 10
# 超时提醒占位保留期（秒）：同一节点任务一天最多提醒一次
FLOW_REMIND_CACHE_SECONDS = 60 * 60 * 24
# 发起通知节流窗口（秒）：防重复提交刷屏
FLOW_NOTIFY_THROTTLE_SECONDS = 60
# 统计默认回看窗口（天）
FLOW_STATS_WINDOW_DAYS = 30
# 条件运算符白名单
CONDITION_OPS = ("eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "contains", "is_empty", "not_empty")


def _models():
    """按需返回模型类容器：延迟导入避免模块导入期循环依赖。

    注意用属性访问（``_models().Task``）而非元组解包——本模块大量使用 ``_()``
    做翻译，元组解包里的 ``_`` 占位符会覆盖翻译函数（历史踩坑）。
    """
    from system.models.approval import ApprovalFlow, ApprovalFlowNode, ApprovalInstance, ApprovalNodeTask

    return SimpleNamespace(Flow=ApprovalFlow, Node=ApprovalFlowNode, Instance=ApprovalInstance, Task=ApprovalNodeTask)


def _users():
    from system.models import UserInfo

    return UserInfo


# ---------------------------------------------------------------- 条件与审批人解析


def _as_str_list(value) -> list:
    """条件比较统一转字符串列表：标量 → [str(value)]，列表 → 逐项 str（in/not_in 用）。"""
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return [str(value)]


def eval_condition(condition, form_data) -> bool:
    """节点条件求值：空条件恒真；未知运算符/取值失败一律返回 False（不经过该节点）。

    表达式：``{"field": "amount", "op": "gte", "value": 1000}``；op 白名单见 CONDITION_OPS。
    op=in/not_in 时两侧都按「字符串集合」比较（表单值可以是标量或列表）。
    """
    if not condition or not isinstance(condition, dict):
        return True
    field = condition.get("field")
    if not field:
        return True
    op = condition.get("op") or "eq"
    expect = condition.get("value")
    actual = (form_data or {}).get(field)

    try:
        if op == "eq":
            return str(actual) == str(expect)
        if op == "ne":
            return str(actual) != str(expect)
        if op == "in":
            return bool(set(_as_str_list(actual)) & set(_as_str_list(expect)))
        if op == "not_in":
            return not bool(set(_as_str_list(actual)) & set(_as_str_list(expect)))
        if op == "contains":
            return str(expect) in str(actual)
        if op == "is_empty":
            return actual in (None, "", [], {})
        if op == "not_empty":
            return actual not in (None, "", [], {})
        if op in ("gt", "gte", "lt", "lte"):
            left, right = float(actual), float(expect)
            return {"gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right}[op]
    except (TypeError, ValueError):
        return False
    logger.warning("approval flow: unknown condition op %s, skip node", op)
    return False


def _split_values(raw) -> list:
    """解析 assignee_value / 表单字段值：逗号分隔字符串或列表，去空去重保序。"""
    if raw is None:
        return []
    values = raw if isinstance(raw, (list, tuple)) else str(raw).replace("，", ",").split(",")
    result = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def resolve_assignees(node, applicant, form_data) -> list:
    """节点候选审批人（按 assignee_type 解析；始终剔除申请人本人与停用用户）。

    结果为空说明该节点无人可审，调用方必须拒绝发起（fail-closed）。
    """
    UserInfo = _users()
    queryset = UserInfo.objects.filter(is_active=True)
    assignee_type = node.assignee_type

    if assignee_type == node.AssigneeType.ROLE:
        codes = _split_values(node.assignee_value)
        if not codes:
            return []
        queryset = queryset.filter(roles__is_active=True, roles__code__in=codes).distinct()
    elif assignee_type == node.AssigneeType.USER:
        names = _split_values(node.assignee_value)
        if not names:
            return []
        queryset = queryset.filter(username__in=names)
    elif assignee_type == node.AssigneeType.LEADER:
        dept = getattr(applicant, "dept", None)
        leader = getattr(dept, "leader", None) if dept else None
        if leader is None:
            return []
        queryset = queryset.filter(pk=leader.pk)
    elif assignee_type == node.AssigneeType.FIELD:
        names = _split_values((form_data or {}).get(node.assignee_value))
        if not names:
            return []
        queryset = queryset.filter(username__in=names)
    else:
        return []

    return list(queryset.exclude(pk=applicant.pk).order_by("pk"))


def matching_nodes(flow, form_data) -> list:
    """按 order 升序返回条件命中的节点（发起时用于校验 + 取首节点）。"""
    return [node for node in flow.nodes.all().order_by("order") if eval_condition(node.condition, form_data)]


def next_node(flow, after_order, form_data):
    """当前节点之后第一个条件命中的节点（无则返回 None = 流程结束）。"""
    for node in flow.nodes.filter(order__gt=after_order).order_by("order"):
        if eval_condition(node.condition, form_data):
            return node
    return None


def validate_form(flow, form_data) -> str:
    """按 form_schema 校验表单：必填缺失 / key 非法返回错误文案，通过返回 None。"""
    if form_data is None:
        form_data = {}
    if not isinstance(form_data, dict):
        return str(_("Form data must be an object"))
    for item in flow.form_schema or []:
        key = (item or {}).get("key")
        if not key:
            continue
        if item.get("required"):
            value = form_data.get(key)
            if value in (None, "", [], {}):
                return str(_("Field {} is required").format(item.get("label") or key))
    return None


# ---------------------------------------------------------------- 实例推进


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
    审计任务（comment 注明自动通过），保证行为可追溯。
    """
    ApprovalNodeTask = _models().Task
    now = timezone.now()
    candidates = resolve_assignees(node, instance.creator, instance.form_data)
    if not candidates:
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

    tasks = [
        ApprovalNodeTask.objects.create(
            instance=instance, node=node, node_name=node.name, node_order=node.order, assignee=user
        )
        for user in candidates
    ]
    _notify(candidates, "submitted", instance)
    _invalidate_pending_count(candidates)
    return bool(tasks)


def create_instance(*, flow, applicant, title, form_data):
    """发起申请：校验表单与全部可达节点候选，建实例并进入首节点。

    返回 (instance, error)：error 为 None 表示成功。候选校验 fail-closed——
    任一可达节点无人可审即拒绝发起（避免在途中卡死或静默放行）。
    """
    ApprovalInstance = _models().Instance

    if not flow.is_active:
        return None, str(_("The flow is disabled"))
    error = validate_form(flow, form_data)
    if error:
        return None, error
    nodes = matching_nodes(flow, form_data)
    if not nodes:
        return None, str(_("The flow has no available node"))
    for node in nodes:
        if not resolve_assignees(node, applicant, form_data):
            return None, str(_("No available approver for node {}").format(node.name))

    instance = ApprovalInstance.objects.create(
        flow=flow,
        flow_name=flow.name,
        title=(title or "").strip()[:128],
        form_data=form_data or {},
        creator=applicant,
        current_node=nodes[0],
    )
    _enter_node(instance, nodes[0])
    return instance, None


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
        following = next_node(instance.flow, node.order, instance.form_data)
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
    if node.approve_type == node.ApproveType.OR:
        # 或签：任一通过即节点通过，其余待办作废
        _cancel_pending_tasks(instance, node=node)
        _invalidate_pending_count()
        _advance(instance, node)
    else:
        remaining = ApprovalNodeTask.objects.filter(
            instance=instance, node=node, status=ApprovalNodeTask.Status.PENDING
        ).exists()
        _invalidate_pending_count()
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


# ---------------------------------------------------------------- 查询口径


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


# ---------------------------------------------------------------- 定时任务


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
