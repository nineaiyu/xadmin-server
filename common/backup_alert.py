#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""备份失败告警（S2）：备份容器回调 → 站内信 + 邮件通知超管。

背景：备份/异地同步失败原先只落 WARN 日志（deployment.md 检查清单遗留项），
无人值守时故障不会被发现。db_backup.sh 在失败点调用
`POST /api/common/api/backup-alert`（独立令牌 `BACKUP_ALERT_TOKEN`），
服务端按 `task_failure` 的 60s 节流范式发布 `BackupFailureMessage`。

节流：同一来源（source）60 秒内只告警一次——备份循环 6h 一轮、每轮可能连续触发
多个失败点（dump 失败 → 校验失败），节流避免一轮内多封邮件。
"""

import hashlib
import logging

from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from notifications.services import BACKEND, SystemMessage, SystemMsgSubscription, register_message
from system.services import get_active_superuser_queryset

logger = logging.getLogger("xadmin")

BACKUP_ALERT_THROTTLE_SECONDS = 60


@register_message
class BackupFailureMessage(SystemMessage):
    """备份失败告警：站点信 + 邮件（默认订阅者 = 全部在用超管）。"""

    category = "Monitor"
    category_label = _("Monitor")
    message_type_label = _("Backup failure alert")

    def __init__(self, payload: dict):
        self.payload = payload or {}

    def get_html_msg(self) -> dict:
        source = self.payload.get("source") or "backup"
        event = self.payload.get("event") or _("Unknown failure")
        host = self.payload.get("host") or "-"
        occurred_at = self.payload.get("time") or "-"
        detail = (self.payload.get("detail") or "")[:2000]
        subject = _("Backup failure alert: {}").format(event)
        message = (
            f"<p>{_('Source')}: <code>{source}</code></p>"
            f"<p>{_('Event')}: <code>{event}</code></p>"
            f"<p>{_('Host')}: <code>{host}</code></p>"
            f"<p>{_('Time')}: <code>{occurred_at}</code></p>"
        )
        if detail:
            message += f"<pre style='white-space:pre-wrap'>{detail}</pre>"
        message += f"<p>{_('Please check the backup container logs and the backup directory.')}</p>"
        return {"subject": subject, "message": message}

    def get_site_msg_msg(self):
        info = self.get_html_msg()
        info["level"] = "danger"
        return info

    @classmethod
    def post_insert_to_db(cls, subscription: SystemMsgSubscription):
        admins = get_active_superuser_queryset()
        subscription.users.add(*admins)
        subscription.receive_backends = [BACKEND.SITE_MSG, BACKEND.EMAIL]
        subscription.save()

    def publish(self, is_async=False):
        """发布告警；订阅缺失/收件人为空时自愈补建（存量库 post_migrate 早于本消息注册）。

        与 SensitiveOperationMessage 同范式：不自愈会让备份告警在存量库永久静默。
        """
        subscription, created = SystemMsgSubscription.objects.get_or_create(message_type=self.get_message_type())
        if created or not subscription.users.exists():
            self.post_insert_to_db(subscription)
        super().publish(is_async=is_async)

    @classmethod
    def gen_test_msg(cls):
        return cls({"source": "db-backup", "event": "demo backup failure", "host": "test"})


def notify_backup_failure(payload: dict) -> bool:
    """按 60s 节流发布备份失败告警；返回是否真正发布（被节流为 False）。

    节流键对来源做哈希：source 由调用方（备份脚本）传入，可能含空格/引号等
    对部分缓存后端非法的字符（memcached 会抛 CacheKeyWarning）。
    """
    source = str((payload or {}).get("source") or "backup")[:64]
    ident = hashlib.md5(source.encode("utf-8")).hexdigest()[:16]
    if not cache.add(f"backup_failure_alert_{ident}", 1, BACKUP_ALERT_THROTTLE_SECONDS):
        return False
    try:
        BackupFailureMessage(payload).publish(is_async=True)
    except Exception:  # noqa: BLE001 告警链路故障不影响上报响应（脚本仅记 WARN）
        logger.warning("send backup failure alert failed", exc_info=True)
        return False
    return True
