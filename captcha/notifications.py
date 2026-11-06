#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""验证码风险告警消息（系统消息，接既有通知渠道）。"""

from typing import Any

from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from identity.services import get_active_superuser_queryset
from notifications.services import BACKEND, SystemMessage, SystemMsgSubscription, register_message

#: 风险统计键的可读标签（静态映射；未登记的新键回退原键名，告警内容不因缺词条丢失）
_RISK_LABELS: dict[str, Any] = {
    "kind": _("Risk type"),
    "deny_rate": _("Deny rate"),
    "samples": _("Samples"),
    "reason": _("Trigger reason"),
    "ip_hash": _("IP hash"),
}


@register_message
class CaptchaRiskAlertMessage(SystemMessage):
    """验证码风险告警：拦截率异常 / 同 IP 挑战突增。"""

    category = "Security"
    category_label = _("Security")
    message_type_label = _("Captcha risk alert")

    def __init__(self, info: dict[str, Any]):
        self.info = info

    def get_html_msg(self) -> dict[str, Any]:
        subject = _("Captcha risk alert: {}").format(self.info.get("kind"))
        # format_html_join 逐项转义：统计值当前是聚合指标/加盐哈希（无自由文本），
        # 但转义让「值可安全嵌入 HTML」成为结构性保证，后续新增键无需逐个审查
        message = format_html(
            "<p>{}</p><ul>{}</ul>",
            subject,
            format_html_join(
                "",
                "<li>{}: {}</li>",
                ((str(_RISK_LABELS.get(key, key)), str(value)) for key, value in self.info.items()),
            ),
        )
        return {"subject": subject, "message": message}

    def get_site_msg_msg(self) -> Any:
        info = self.get_html_msg()
        info["level"] = "warning"
        return info

    @classmethod
    def post_insert_to_db(cls, subscription: SystemMsgSubscription) -> None:
        subscription.users.add(*get_active_superuser_queryset())
        subscription.receive_backends = [BACKEND.SITE_MSG]
        subscription.save()

    def publish(self, is_async: Any = False) -> None:
        subscription = SystemMsgSubscription.objects.get(message_type=self.get_message_type())
        if not subscription.users.exists():
            self.post_insert_to_db(subscription)
        super().publish(is_async=is_async)

    @classmethod
    def gen_test_msg(cls) -> Any:
        return cls({"kind": "deny_rate", "deny_rate": 0.72, "samples": 128})
