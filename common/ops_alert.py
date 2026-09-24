#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""运维告警（A1）：宿主侧 watcher 回调 → 站内信 + 邮件通知超管。

背景：容器 OOM 等宿主级事件原先只能查 `docker events`（故障演练登记的观察项），
无人值守时不会被发现。宿主侧脚本 `utils/oom_alert.sh` 监听 `docker events` 的
`oom` 事件并调用 `POST /api/common/api/ops-alert`（独立令牌 `OPS_ALERT_TOKEN`），
服务端按 `task_failure` 的 60s 节流范式发布 `OpsAlertMessage`。

节流：同一来源（source）+ 事件（event）60 秒内只告警一次——OOM 风暴（同一容器
被反复杀掉）合并为一条，不同事件互不影响。
"""

import hashlib
import logging

from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from notifications.services import BACKEND, SystemMessage, SystemMsgSubscription, register_message
from system.services import get_active_superuser_queryset

logger = logging.getLogger("xadmin")

OPS_ALERT_THROTTLE_SECONDS = 60


@register_message
class OpsAlertMessage(SystemMessage):
    """运维告警：站点信 + 邮件（默认订阅者 = 全部在用超管）。"""

    category = "Monitor"
    category_label = _("Monitor")
    message_type_label = _("Ops alert")

    def __init__(self, payload: dict):
        self.payload = payload or {}

    @classmethod
    def template_variables(cls) -> tuple:
        """模板可引用的业务变量（与 get_template_vars 同源，缺一会由守护测试拦下）。"""
        return ("source", "event", "host", "time", "detail")

    def get_template_vars(self) -> dict:
        """业务变量取值：detail 与渠道文案同样截断，避免模板把整段堆栈带进短消息渠道。"""
        return {
            "source": self.payload.get("source") or "ops",
            "event": str(self.payload.get("event") or ""),
            "host": self.payload.get("host") or "-",
            "time": self.payload.get("time") or "-",
            "detail": (self.payload.get("detail") or "")[:2000],
        }

    def get_html_msg(self) -> dict:
        # 取值与模板变量同源：渠道默认文案与模板覆盖层不会各写一套
        context = self.get_template_vars()
        event = context["event"] or _("Unknown event")
        subject = _("Ops alert: {}").format(event)
        message = (
            f"<p>{_('Source')}: <code>{context['source']}</code></p>"
            f"<p>{_('Event')}: <code>{event}</code></p>"
            f"<p>{_('Host')}: <code>{context['host']}</code></p>"
            f"<p>{_('Time')}: <code>{context['time']}</code></p>"
        )
        if context["detail"]:
            message += f"<pre style='white-space:pre-wrap'>{context['detail']}</pre>"
        message += f"<p>{_('Please check the container status and host resource usage.')}</p>"
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
        """发布告警；订阅缺失/收件人为空时自愈补建（存量库 post_migrate 早于本消息注册）。"""
        subscription, created = SystemMsgSubscription.objects.get_or_create(message_type=self.get_message_type())
        if created or not subscription.users.exists():
            self.post_insert_to_db(subscription)
        super().publish(is_async=is_async)

    @classmethod
    def gen_test_msg(cls):
        return cls({"source": "container-oom", "event": "demo ops alert", "host": "test"})


def notify_ops_alert(payload: dict) -> bool:
    """按 60s 节流发布运维告警；返回是否真正发布（被节流为 False）。

    节流键取 source + event 的哈希：同来源同事件（如 OOM 风暴）合并为一条，
    后续接入的宿主级告警按事件维度独立计数。
    """
    source = str((payload or {}).get("source") or "ops")[:64]
    event = str((payload or {}).get("event") or "")[:128]
    ident = hashlib.md5(f"{source}|{event}".encode()).hexdigest()[:16]
    if not cache.add(f"ops_alert_{ident}", 1, OPS_ALERT_THROTTLE_SECONDS):
        return False
    try:
        OpsAlertMessage(payload).publish(is_async=True)
    except Exception:  # noqa: BLE001 告警链路故障不影响上报响应（脚本仅记 WARN）
        logger.warning("send ops alert failed", exc_info=True)
        return False
    # 出站 Webhook：运维告警事件（emit 全程吞异常）
    from system.utils.webhook import emit_webhook_event

    emit_webhook_event("system.ops_alert", payload or {})
    return True
