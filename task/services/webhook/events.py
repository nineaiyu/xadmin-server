#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""出站 Webhook 事件目录：key → 契约（label / version / fields）与纯数据工具。

- 新增事件 = 加一条 + 在信号源接线 emit_webhook_event；
- payload 外壳固定 ``{event, schema_version, occurred_at, data}``，version 取自本表；
- fields 是 data 的字段契约（required=True 必须出现，emit 时校验只告警不阻断）；
- **破坏性变更 = 新增 ``xxx.v2`` 事件**（旧 key 至少保留一个发布窗口），不改老契约语义。
"""

from typing import Any

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

EVENT_CATALOG = {
    "user.login_succeeded": {
        "label": _("Login succeeded"),
        "version": 1,
        "fields": {
            "username": {"type": "string", "required": True, "description": _("Username")},
            "ip": {"type": "string", "required": True, "description": _("Client IP address")},
        },
    },
    "user.login_failed": {
        "label": _("Login failed"),
        "version": 1,
        "fields": {
            "username": {"type": "string", "required": True, "description": _("Username")},
            "ip": {"type": "string", "required": True, "description": _("Client IP address")},
        },
    },
    "approval.submitted": {
        "label": _("Approval submitted"),
        "version": 1,
        "fields": {
            "approval_id": {"type": "string", "required": True, "description": _("Approval id")},
            "module": {"type": "string", "required": True, "description": _("Business module")},
            "path": {"type": "string", "required": False, "description": _("Detail path")},
            "status": {"type": "string", "required": True, "description": _("Approval status")},
            "creator": {"type": "string", "required": True, "description": _("Creator username")},
        },
    },
    "approval.approved": {
        "label": _("Approval approved"),
        "version": 1,
        "fields": {
            "approval_id": {"type": "string", "required": True, "description": _("Approval id")},
            "module": {"type": "string", "required": True, "description": _("Business module")},
            "path": {"type": "string", "required": False, "description": _("Detail path")},
            "status": {"type": "string", "required": True, "description": _("Approval status")},
            "creator": {"type": "string", "required": True, "description": _("Creator username")},
        },
    },
    "approval.rejected": {
        "label": _("Approval rejected"),
        "version": 1,
        "fields": {
            "approval_id": {"type": "string", "required": True, "description": _("Approval id")},
            "module": {"type": "string", "required": True, "description": _("Business module")},
            "path": {"type": "string", "required": False, "description": _("Detail path")},
            "status": {"type": "string", "required": True, "description": _("Approval status")},
            "creator": {"type": "string", "required": True, "description": _("Creator username")},
        },
    },
    "approval.cancelled": {
        "label": _("Approval cancelled"),
        "version": 1,
        "fields": {
            "approval_id": {"type": "string", "required": True, "description": _("Approval id")},
            "module": {"type": "string", "required": True, "description": _("Business module")},
            "path": {"type": "string", "required": False, "description": _("Detail path")},
            "status": {"type": "string", "required": True, "description": _("Approval status")},
            "creator": {"type": "string", "required": True, "description": _("Creator username")},
        },
    },
    # 敏感操作：payload 即操作日志摘要（字段可能缺省，全部可选）
    "security.sensitive_operation": {
        "label": _("Sensitive operation alert"),
        "version": 1,
        "fields": {
            "module": {"type": "string", "required": False, "description": _("Business module")},
            "path": {"type": "string", "required": False, "description": _("URL path")},
            "method": {"type": "string", "required": False, "description": _("HTTP method")},
            "ipaddress": {"type": "string", "required": False, "description": _("Client IP address")},
        },
    },
    # 备份失败：payload 由备份脚本上报（字段随来源变化，全部可选）
    "system.backup_failure": {
        "label": _("Backup failure"),
        "version": 1,
        "fields": {
            "source": {"type": "string", "required": False, "description": _("Backup source")},
            "event": {"type": "string", "required": False, "description": _("Failure summary")},
            "host": {"type": "string", "required": False, "description": _("Host name")},
        },
    },
    # 运维告警：宿主侧 watcher（容器 OOM 等）事件，由 notify_ops_alert 发射
    "system.ops_alert": {
        "label": _("Ops alert"),
        "version": 1,
        "fields": {
            "source": {"type": "string", "required": False, "description": _("Alert source")},
            "event": {"type": "string", "required": False, "description": _("Event summary")},
            "host": {"type": "string", "required": False, "description": _("Host name")},
        },
    },
    # 流程审批引擎：实例级事件（提交/终态），payload 只含摘要不含 form_data
    "flow.submitted": {
        "label": _("Flow application submitted"),
        "version": 1,
        "fields": {
            "instance_no": {"type": "string", "required": True, "description": _("Instance number")},
            "title": {"type": "string", "required": True, "description": _("Instance title")},
            "flow_name": {"type": "string", "required": True, "description": _("Flow name")},
            "status": {"type": "string", "required": True, "description": _("Instance status")},
            "creator": {"type": "string", "required": True, "description": _("Creator username")},
            "current_node": {"type": "string", "required": False, "description": _("Current node")},
            "reason": {"type": "string", "required": False, "description": _("Finish reason")},
        },
    },
    "flow.approved": {
        "label": _("Flow application approved"),
        "version": 1,
        "fields": {
            "instance_no": {"type": "string", "required": True, "description": _("Instance number")},
            "title": {"type": "string", "required": True, "description": _("Instance title")},
            "flow_name": {"type": "string", "required": True, "description": _("Flow name")},
            "status": {"type": "string", "required": True, "description": _("Instance status")},
            "creator": {"type": "string", "required": True, "description": _("Creator username")},
            "current_node": {"type": "string", "required": False, "description": _("Current node")},
            "reason": {"type": "string", "required": False, "description": _("Finish reason")},
        },
    },
    "flow.rejected": {
        "label": _("Flow application rejected"),
        "version": 1,
        "fields": {
            "instance_no": {"type": "string", "required": True, "description": _("Instance number")},
            "title": {"type": "string", "required": True, "description": _("Instance title")},
            "flow_name": {"type": "string", "required": True, "description": _("Flow name")},
            "status": {"type": "string", "required": True, "description": _("Instance status")},
            "creator": {"type": "string", "required": True, "description": _("Creator username")},
            "current_node": {"type": "string", "required": False, "description": _("Current node")},
            "reason": {"type": "string", "required": False, "description": _("Finish reason")},
        },
    },
    "flow.cancelled": {
        "label": _("Flow application cancelled"),
        "version": 1,
        "fields": {
            "instance_no": {"type": "string", "required": True, "description": _("Instance number")},
            "title": {"type": "string", "required": True, "description": _("Instance title")},
            "flow_name": {"type": "string", "required": True, "description": _("Flow name")},
            "status": {"type": "string", "required": True, "description": _("Instance status")},
            "creator": {"type": "string", "required": True, "description": _("Creator username")},
            "current_node": {"type": "string", "required": False, "description": _("Current node")},
            "reason": {"type": "string", "required": False, "description": _("Finish reason")},
        },
    },
    # 退回重审：实例回退到已途经节点（非终态，status 恒为 PENDING，reason 为退回原因）
    "flow.returned": {
        "label": _("Flow application returned"),
        "version": 1,
        "fields": {
            "instance_no": {"type": "string", "required": True, "description": _("Instance number")},
            "title": {"type": "string", "required": True, "description": _("Instance title")},
            "flow_name": {"type": "string", "required": True, "description": _("Flow name")},
            "status": {"type": "string", "required": True, "description": _("Instance status")},
            "creator": {"type": "string", "required": True, "description": _("Creator username")},
            "current_node": {"type": "string", "required": False, "description": _("Current node")},
            "reason": {"type": "string", "required": False, "description": _("Finish reason")},
        },
    },
    # 节点无候选自动通过（治理告警：配置缺口导致流程静默放行，实例仍为 PENDING）
    "flow.node_auto_approved": {
        "label": _("Flow node auto-approved"),
        "version": 1,
        "fields": {
            "instance_no": {"type": "string", "required": True, "description": _("Instance number")},
            "title": {"type": "string", "required": True, "description": _("Instance title")},
            "flow_name": {"type": "string", "required": True, "description": _("Flow name")},
            "status": {"type": "string", "required": True, "description": _("Instance status")},
            "creator": {"type": "string", "required": True, "description": _("Creator username")},
            "node_name": {"type": "string", "required": True, "description": _("Node name")},
            "current_node": {"type": "string", "required": False, "description": _("Current node")},
            "reason": {"type": "string", "required": False, "description": _("Finish reason")},
        },
    },
    # 开放平台：应用每日配额达阈值（软告警，不阻断）
    "api_quota.warning": {
        "label": _("API application quota warning"),
        "version": 1,
        "fields": {
            "application": {"type": "string", "required": True, "description": _("Application name")},
            "client_id": {"type": "string", "required": True, "description": _("Client id")},
            "used": {"type": "integer", "required": True, "description": _("Used requests today")},
            "quota": {"type": "integer", "required": True, "description": _("Daily quota")},
        },
    },
    # 连接测试事件（订阅管理页「测试」按钮），不在业务信号源接线
    "webhook.ping": {
        "label": _("Webhook ping"),
        "version": 1,
        "fields": {
            "subscription": {"type": "string", "required": True, "description": _("Subscription name")},
        },
    },
}


def get_event_label(event: str) -> str:
    entry = EVENT_CATALOG.get(event)
    if not entry:
        return event
    return str(entry.get("label") or event)


def event_catalog_payload() -> list[Any]:
    """事件目录（管理页下拉 / 文档生成共用）：key/label/version/fields。"""
    catalog = []
    for key, entry in EVENT_CATALOG.items():
        fields = {
            name: {
                "type": spec.get("type", "string"),
                "required": bool(spec.get("required")),
                "description": str(spec.get("description") or ""),
            }
            for name, spec in (entry.get("fields") or {}).items()
        }
        catalog.append(
            {"key": key, "label": str(entry.get("label") or key), "version": entry.get("version", 1), "fields": fields}
        )
    return catalog


def validate_events(events: Any) -> list[Any]:
    if not isinstance(events, list) or not events:
        raise ValidationError(_("Webhook events cannot be empty"))
    for event in events:
        if event not in EVENT_CATALOG:
            raise ValidationError(_("Unknown webhook event: {}").format(event))
    return [str(event) for event in events]
