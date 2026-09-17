#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全量审批流引擎：常量与模型按需取数。"""

from types import SimpleNamespace

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
# 实例终态 → 出站 Webhook 事件（flow.*）；PENDING 不经 _finish_instance 不映射
_FLOW_FINISH_EVENTS = {
    "APPROVED": "flow.approved",
    "REJECTED": "flow.rejected",
    "CANCELLED": "flow.cancelled",
}


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


def _delegations():
    from system.models import ApprovalDelegation

    return ApprovalDelegation
