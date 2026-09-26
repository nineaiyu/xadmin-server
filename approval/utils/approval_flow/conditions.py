#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全量审批流引擎：条件求值与审批人解析。"""

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

from .constants import _delegations, _users

logger = get_logger(__name__)


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
    return [user for user, _source in resolve_assignee_pairs(node, applicant, form_data)]


def resolve_assignee_pairs(node, applicant, form_data) -> list:
    """节点候选审批人（含委托来源）：``[(user, delegate_from | None)]``。

    delegate_from 非空表示该候选由原审批人委托代理（任务落库时记录，轨迹标注
    「由 X 代理」）；无委托时为 None。其余解析语义与 resolve_assignees 一致。
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

    resolved = list(queryset.exclude(pk=applicant.pk).order_by("pk"))
    return _expand_delegations(resolved, node, applicant)


def _expand_delegations(users, node, applicant) -> list:
    """委托代理展开（审批流三期）：生效委托用代理人替换原审批人。

    - 仅「生效中」委托参与：is_active + start<=now<=end + 流程范围命中（空 = 全部流程）；
    - 代理人若为申请人本人或已停用 → 丢弃该候选（申请人不能审批自己的节点，语义不变）；
    - 代理人自身再委托不生效（不递归，防环）；
    - 无委托记录时一次批量查询后原样返回（存量行为零变化）；
    - 发生替换时同时回传原审批人（delegate_from），供任务与轨迹标注代审来源。
    """
    if not users:
        return users
    ApprovalDelegation = _delegations()
    now = timezone.now()
    flow_code = getattr(getattr(node, "flow", None), "code", "")
    rows = ApprovalDelegation.objects.filter(
        delegator__in=users, is_active=True, start_time__lte=now, end_time__gte=now
    ).select_related("delegate")
    by_delegator = {}
    for row in rows:
        codes = row.flow_codes or []
        if codes and flow_code not in codes:
            continue
        by_delegator[row.delegator_id] = row.delegate

    expanded: dict = {}
    for user in users:
        target = by_delegator.get(user.pk) or user
        if target.pk == applicant.pk or not target.is_active:
            continue
        source = user if target.pk != user.pk else None
        expanded[target.pk] = (target, source)
    return list(expanded.values())


def matching_nodes(flow, form_data) -> list:
    """按 order 升序返回条件命中的节点（发起时用于校验 + 取首节点）。"""
    return [node for node in flow.nodes.all().order_by("order") if eval_condition(node.condition, form_data)]


def next_node(flow, after_order, form_data, node=None):
    """当前节点的下一节点；返回 None = 流程结束。

    二期路由优先：node.routes 逐条求值，首个命中跳转 target
    （排他网关）；全部未命中或无 routes 时回退一期线性语义（order 之后首个
    条件命中节点）。target 无效（节点已不存在）记日志后同样回退线性。
    """
    if node is not None:
        for route in node.routes or []:
            if not eval_condition(route.get("condition"), form_data):
                continue
            target_order = route.get("target")
            target = flow.nodes.filter(order=target_order).first()
            if target is not None:
                return target
            logger.warning(
                "approval flow route target missing, fallback to linear. flow:%s node:%s target:%s",
                flow.pk,
                node.pk,
                target_order,
            )
    for following in flow.nodes.filter(order__gt=after_order).order_by("order"):
        if eval_condition(following.condition, form_data):
            return following
    return None


def simulate_path(flow, form_data, node=None) -> list:
    """按 form_data 模拟推进，返回途经节点序列（发起预校验 + 步数兜底）。

    排他网关在给定 form_data 下出口唯一，路径确定；步数上限 = 节点数 + 1，
    超限视为路由成环（fail-closed：发起报错，环配置在保存时已被校验拦截，
    此处兜底历史数据）。
    """
    nodes = matching_nodes(flow, form_data)
    if not nodes:
        return []
    limit = flow.nodes.count() + 1
    path = [nodes[0]]
    current = nodes[0]
    while len(path) <= limit:
        following = next_node(flow, current.order, form_data, node=current)
        if following is None:
            break
        path.append(following)
        current = following
    else:
        return None
    return path


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
