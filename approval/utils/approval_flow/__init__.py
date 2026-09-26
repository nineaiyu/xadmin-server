#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全量审批流引擎一期：流程实例推进。

模型关系：ApprovalFlow（定义）→ ApprovalFlowNode（顺序节点，节点级条件）→
ApprovalInstance（一次申请）→ ApprovalNodeTask（一行一个候选审批人）。

与轻量敏感操作审批（system/utils/approval.py 的一次性令牌）完全独立：
- 令牌审批面向「拦截业务请求 → 批准后重发」，无表单、无多级；
- 本引擎面向业务表单（请假/报销类），无请求重放，状态机完整。

关键语义：
- 条件分支（二期）：节点 routes 为排他网关出口路由表（逐条求值首个
  命中即跳转 target），全不命中回退一期线性语义；
- 或签 OR：任一 APPROVED 即节点通过，其余 PENDING 行置 CANCELLED；
- 会签 AND：全部 APPROVED 才通过；任一行 REJECTED → 实例驳回（终态）；
- 申请人不能审批自己的节点（候选解析时剔除申请人；无候选在发起时即报错）；
- 驳回/撤回均终止实例；加签在「当前节点」追加候选（会签语义下必须通过）。

并发说明：审批动作（approve / reject / cancel / add_sign）在**实例行锁**
（``select_for_update``）内串行执行——同一实例的并发操作不会重复推进（下一节点
重复建任务组）；终态跃迁另有状态 CAS 兜底（重复置终态只生效一次，Webhook/业务
回调不重复投递）。sqlite（单测/E2E 库）忽略行锁，此时退化为「任务级 CAS +
终态 CAS」语义。

本包按职责拆分（constants / conditions / engine / extra_actions / queries / periodic），
对外 API 由本文件统一再导出，导入路径保持 ``system.utils.approval_flow`` 不变。
"""

from .conditions import (
    eval_condition,
    matching_nodes,
    next_node,
    resolve_assignee_pairs,
    resolve_assignees,
    simulate_path,
    validate_form,
)
from .constants import (
    CONDITION_OPS,
    FLOW_NOTIFY_THROTTLE_SECONDS,
    FLOW_PENDING_COUNT_CACHE_SECONDS,
    FLOW_REMIND_CACHE_SECONDS,
    FLOW_STATS_WINDOW_DAYS,
)
from .engine import (
    _emit_flow_event as _emit_flow_event,  # noqa: PLC0414 显式再导出（测试按私有名导入）
)
from .engine import (
    approve_task,
    cancel_instance,
    create_instance,
    reject_task,
)
from .extra_actions import add_sign, transfer_task, urge_instance
from .periodic import clean_finished_instances, remind_pending_tasks
from .queries import (
    done_tasks_for,
    instance_stats,
    node_progress_for,
    pending_count_for,
    pending_tasks_for,
    visible_instances_for,
)

__all__ = [
    "CONDITION_OPS",
    "FLOW_NOTIFY_THROTTLE_SECONDS",
    "FLOW_PENDING_COUNT_CACHE_SECONDS",
    "FLOW_REMIND_CACHE_SECONDS",
    "FLOW_STATS_WINDOW_DAYS",
    "add_sign",
    "approve_task",
    "cancel_instance",
    "clean_finished_instances",
    "create_instance",
    "done_tasks_for",
    "eval_condition",
    "instance_stats",
    "matching_nodes",
    "next_node",
    "node_progress_for",
    "pending_count_for",
    "pending_tasks_for",
    "remind_pending_tasks",
    "reject_task",
    "resolve_assignee_pairs",
    "resolve_assignees",
    "simulate_path",
    "transfer_task",
    "urge_instance",
    "validate_form",
    "visible_instances_for",
]
