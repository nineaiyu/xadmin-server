#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""出站 Webhook 核心工具：事件目录、发射口、签名。

安全口径：
- `emit_webhook_event` 全程吞异常——Webhook 任何故障不得影响宿主动作；
- secret 沿用 Setting 的 signer 值级加密（落库密文、投递时解密）；
- 签名 GitHub 风格：`sha256=HMAC(secret, "{timestamp}.{raw_body}")`，
  timestamp 参与签名防重放（接收方建议 5 分钟窗口校验）；
- URL 白名单在写入侧校验（https 强制，loopback http 例外供联调）。
"""

import hashlib
import hmac
import time

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.base.utils import signer
from common.utils import get_logger

logger = get_logger(__name__)

# 事件目录：key → 契约（label / version / fields）。# - 新增事件 = 加一条 + 在信号源接线 emit_webhook_event；
# - payload 外壳固定 ``{event, schema_version, occurred_at, data}``，version 取自本表；
# - fields 是 data 的字段契约（required=True 必须出现，emit 时校验只告警不阻断）；
# - **破坏性变更 = 新增 ``xxx.v2`` 事件**（旧 key 至少保留一个发布窗口），不改老契约语义。
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

LOOPBACK_HOSTS = ("127.0.0.1", "localhost")

# 重试策略：countdown = min(60 × 2^attempt, 3600)，上限 5 次尝试
MAX_ATTEMPTS = 5
RETRY_BASE_SECONDS = 60
RETRY_MAX_SECONDS = 3600


def get_event_label(event: str) -> str:
    entry = EVENT_CATALOG.get(event)
    if not entry:
        return event
    return str(entry.get("label") or event)


def event_catalog_payload() -> list:
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


def validate_url(url: str) -> str:
    """写入侧 URL 校验：https 强制，loopback http 例外（联调/测试）。"""
    url = str(url or "").strip()
    if url.startswith("https://"):
        return url
    for host in LOOPBACK_HOSTS:
        if url.startswith(f"http://{host}:") or url == f"http://{host}":
            return url
    raise ValidationError(_("Webhook url must use https (loopback http is allowed for testing)"))


def validate_events(events) -> list:
    if not isinstance(events, list) or not events:
        raise ValidationError(_("Webhook events cannot be empty"))
    for event in events:
        if event not in EVENT_CATALOG:
            raise ValidationError(_("Unknown webhook event: {}").format(event))
    return [str(event) for event in events]


def encrypt_secret(secret: str) -> str:
    # signer.encrypt 返回 base64 bytes，落库前转 str
    return signer.encrypt(str(secret)).decode("utf-8")


def decrypt_secret(encrypted: str) -> str:
    try:
        return signer.decrypt(encrypted)
    except Exception:  # noqa: BLE001 密文损坏按无密钥处理（签名失败即可观测）
        logger.warning("webhook secret decrypt failed")
        return ""


def sign_payload(secret: str, body: bytes, timestamp: int | None = None) -> tuple:
    """返回 (signature_header_value, timestamp)。"""
    ts = int(timestamp if timestamp is not None else time.time())
    mac = hmac.new(str(secret).encode("utf-8"), f"{ts}.".encode() + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}", ts


def emit_webhook_event(event: str, data: dict) -> int:
    """事件发射唯一入口：为每个订阅该事件的 active 订阅创建投递并派发任务。

    全程吞异常：Webhook 链路任何故障只记日志，绝不影响宿主动作。
    :return: 创建的投递数（仅测试/观测用途）。
    """
    try:
        if event not in EVENT_CATALOG:
            logger.warning("emit unknown webhook event: %s", event)
            return 0
        from system.models.webhook import WebhookDelivery, WebhookSubscription

        # events 为 JSON 列表：contains lookup 在 SQLite 不可用，改为内存过滤
        # （订阅量为管理面个位数，无需 JSON 索引）
        subscriptions = [
            sub for sub in WebhookSubscription.objects.filter(is_active=True) if event in (sub.events or [])
        ]
        contract = EVENT_CATALOG.get(event) or {}
        # 契约校验只告警不阻断：宿主链路永不受 webhook 契约缺陷影响（守护测试兜底）
        missing = [
            name
            for name, spec in (contract.get("fields") or {}).items()
            if spec.get("required") and name not in (data or {})
        ]
        if missing:
            logger.warning("webhook event %s missing required fields: %s", event, ", ".join(missing))
        occurred_at = timezone.now().isoformat()
        payloads = [
            WebhookDelivery(
                subscription=sub,
                event=event,
                payload={
                    "event": event,
                    "schema_version": contract.get("version", 1),
                    "occurred_at": occurred_at,
                    "data": data or {},
                },
            )
            for sub in subscriptions
        ]
        deliveries = WebhookDelivery.objects.bulk_create(payloads)
        from system.webhook_tasks import deliver_webhook

        for delivery in deliveries:
            deliver_webhook.apply_async(kwargs={"delivery_id": str(delivery.pk)}, task_id=str(delivery.pk))
        return len(deliveries)
    except Exception:  # noqa: BLE001 发射口绝不打断宿主动作
        logger.warning("emit webhook event failed: %s", event, exc_info=True)
        return 0
