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

from django.utils import timezone

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from common.base.utils import signer
from common.utils import get_logger

logger = get_logger(__name__)

# 事件目录（key → 中文名）。新增事件 = 加一行 + 在信号源接线 emit_webhook_event。
EVENT_CATALOG = {
    "user.login_succeeded": _("Login succeeded"),
    "user.login_failed": _("Login failed"),
    "approval.submitted": _("Approval submitted"),
    "approval.approved": _("Approval approved"),
    "approval.rejected": _("Approval rejected"),
    "approval.cancelled": _("Approval cancelled"),
    "security.sensitive_operation": _("Sensitive operation alert"),
    "system.backup_failure": _("Backup failure"),
    # 流程审批引擎：实例级事件（提交/终态），payload 只含摘要不含 form_data
    "flow.submitted": _("Flow application submitted"),
    "flow.approved": _("Flow application approved"),
    "flow.rejected": _("Flow application rejected"),
    "flow.cancelled": _("Flow application cancelled"),
    # 连接测试事件（订阅管理页「测试」按钮），不在业务信号源接线
    "webhook.ping": _("Webhook ping"),
}

LOOPBACK_HOSTS = ("127.0.0.1", "localhost")

# 重试策略：countdown = min(60 × 2^attempt, 3600)，上限 5 次尝试
MAX_ATTEMPTS = 5
RETRY_BASE_SECONDS = 60
RETRY_MAX_SECONDS = 3600


def get_event_label(event: str) -> str:
    return str(EVENT_CATALOG.get(event, event))


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
    mac = hmac.new(str(secret).encode("utf-8"), f"{ts}.".encode("utf-8") + body, hashlib.sha256)
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
        occurred_at = timezone.now().isoformat()
        payloads = [
            WebhookDelivery(
                subscription=sub, event=event, payload={"event": event, "occurred_at": occurred_at, "data": data or {}}
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
