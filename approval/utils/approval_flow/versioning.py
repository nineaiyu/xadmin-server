#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全量审批流引擎：定义版本化写入（节点有效区间）。

改版统一口径（同一事务内）：

1. 当前生效节点行收口（``version_to = 新版本``）——**不物理删除**，在途实例按自身
   ``flow_version`` 仍能取到旧行；
2. 按新版本落新行（``version_from = 新版本``）；
3. ``flow.version`` +1 并落全量快照（变更审计追溯 + 回滚依据）。

读侧对应：``ApprovalFlowNode.objects`` 只返回当前生效行，按版本推进/历史查询显式走
``ApprovalFlowNode.all_objects.effective_at(V)``。
"""

from typing import Any

from django.db import transaction
from django.utils import timezone

from approval.models.approval import ApprovalFlowNode, ApprovalFlowVersion


def normalize_cc_users(value: Any, limit: Any = 20) -> list[Any]:
    """抄送人（用户 pk 列表）标准化：去空 / 去重 / 限长。"""
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    result = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result[:limit]


def build_snapshot(flow: Any, nodes: Any) -> dict[str, Any]:
    """定义全量快照：流程元数据 + 表单 + 节点列表（与历史快照格式逐字段一致）。"""
    return {
        "name": flow.name,
        "code": flow.code,
        "is_active": flow.is_active,
        "form_schema": flow.form_schema or [],
        "nodes": [
            {
                "name": (node.get("name") or "").strip()[:64],
                "order": int(node.get("order") or index + 1),
                "approve_type": node.get("approve_type") or ApprovalFlowNode.ApproveType.OR,
                "approve_ratio": int(node.get("approve_ratio") or 100),
                "assignee_type": node.get("assignee_type") or ApprovalFlowNode.AssigneeType.ROLE,
                "assignee_value": (node.get("assignee_value") or "").strip()[:255],
                "condition": node.get("condition") or {},
                "routes": node.get("routes") or [],
                "layout": node.get("layout") or {},
                "timeout_hours": int(node.get("timeout_hours") or 0),
                "timeout_action": node.get("timeout_action") or ApprovalFlowNode.TimeoutAction.NONE,
                "cc_users": normalize_cc_users(node.get("cc_users")),
            }
            for index, node in enumerate(nodes or [])
        ],
    }


def close_active_nodes(flow: Any, version: int) -> int:
    """当前生效行收口到指定版本（``version_to = version``）；返回收口行数。"""
    closed: int = ApprovalFlowNode.all_objects.filter(flow=flow, version_to__isnull=True).update(
        version_to=version, updated_time=timezone.now()
    )
    return closed


def create_node_rows(flow: Any, nodes: Any, version: int) -> None:
    """按节点定义落新行（``version_from = version``，即该版本起生效）。"""
    ApprovalFlowNode.all_objects.bulk_create(
        [
            ApprovalFlowNode(
                flow=flow,
                name=(node.get("name") or "").strip()[:64],
                order=int(node.get("order") or index + 1),
                approve_type=node.get("approve_type") or ApprovalFlowNode.ApproveType.OR,
                approve_ratio=int(node.get("approve_ratio") or 100),
                assignee_type=node.get("assignee_type") or ApprovalFlowNode.AssigneeType.ROLE,
                assignee_value=(node.get("assignee_value") or "").strip()[:255],
                condition=node.get("condition") or {},
                routes=node.get("routes") or [],
                layout=node.get("layout") or {},
                timeout_hours=int(node.get("timeout_hours") or 0),
                timeout_action=node.get("timeout_action") or ApprovalFlowNode.TimeoutAction.NONE,
                cc_users=normalize_cc_users(node.get("cc_users")),
                version_from=version,
            )
            for index, node in enumerate(nodes or [])
        ]
    )


@transaction.atomic  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def apply_definition(flow: Any, nodes: Any, remark: Any, *, snapshot_upsert: bool = False) -> int:
    """定义变更统一入口：收口旧行 + 新版本落行 + 版本号 +1 + 落快照。返回新版本号。

    ``snapshot_upsert=True``（种子/演示命令重灌场景）：loaddata 会把 ``flow.version``
    重置回种子值，同一版本号可能已有快照——就地刷新而不是重复建行（唯一约束会拦）。
    """
    new_version = (flow.version or 0) + 1
    close_active_nodes(flow, new_version)
    create_node_rows(flow, nodes, new_version)
    flow.version = new_version
    flow.save(update_fields=["version", "updated_time"])
    snapshot = build_snapshot(flow, nodes)
    if snapshot_upsert:
        ApprovalFlowVersion.objects.update_or_create(
            flow=flow, version=new_version, defaults={"snapshot": snapshot, "remark": str(remark)}
        )
    else:
        ApprovalFlowVersion.objects.create(flow=flow, version=new_version, snapshot=snapshot, remark=str(remark))
    return new_version


__all__ = [
    "apply_definition",
    "build_snapshot",
    "close_active_nodes",
    "create_node_rows",
    "normalize_cc_users",
]
