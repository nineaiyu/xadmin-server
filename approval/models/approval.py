#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批域模型：轻量敏感操作审批 + 全量审批流引擎一期（兼容壳）。

模型实现已按域拆分到同包模块（行数治理，导入面保持不变）：

- ``approval_request``：ApprovalRequest（轻量敏感操作审批单 / 一次性通行令牌）；
- ``approval_flow_def``：ApprovalFlow / ApprovalFlowNode / ApprovalFlowVersion（流程定义与版本快照）；
- ``approval_instance``：ApprovalInstance / ApprovalNodeTask / ApprovalInstanceComment（实例 / 任务 / 讨论区）；
- ``approval_delegation``：ApprovalDelegation（委托代审）。

``from approval.models.approval import ApprovalInstance`` 等既有导入路径继续可用。

一、ApprovalRequest（轻量：一次性通行令牌）
拦截点在业务执行前建 PENDING 单并通知审批人；审批通过后由原始客户端在有效期内携带
approval_id 重发同一请求，消费令牌后放行。不做服务端请求重放（multipart/大 body 重放
不可靠），含文件的敏感操作不支持审批。状态机：PENDING / APPROVED / REJECTED /
CANCELLED / EXPIRED / FAILED。

二、全量引擎一期
面向业务表单的多级审批：流程定义 = 顺序节点列表（节点级条件表达式决定是否经过该
节点），节点支持或签（任一通过）/ 会签（全部通过）/ 比例会签，审批人支持 角色 /
指定用户 / 申请人上级（部门 leader）/ 表单字段（值为用户名列表）/ 岗位。驳回即终止，
撤回仅限申请人且仅 PENDING。节点任务一行一个候选审批人，加签在当前节点追加候选。
"""

from .approval_delegation import ApprovalDelegation
from .approval_flow_def import (
    ApprovalFlow,
    ApprovalFlowNode,
    ApprovalFlowNodeManager,
    ApprovalFlowNodeQuerySet,
    ApprovalFlowVersion,
)
from .approval_instance import ApprovalInstance, ApprovalInstanceComment, ApprovalNodeTask
from .approval_request import ApprovalRequest

__all__ = [
    "ApprovalDelegation",
    "ApprovalFlow",
    "ApprovalFlowNode",
    "ApprovalFlowNodeManager",
    "ApprovalFlowNodeQuerySet",
    "ApprovalFlowVersion",
    "ApprovalInstance",
    "ApprovalInstanceComment",
    "ApprovalNodeTask",
    "ApprovalRequest",
]
