#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""验证码风险告警消息（系统消息，接既有通知渠道）。"""

from typing import Any

from django.utils.translation import gettext_lazy as _

from identity.services import get_active_superuser_queryset
from notifications.services import BACKEND, SystemMessage, SystemMsgSubscription, register_message


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
        message = "<p>{}</p><ul>{}</ul>".format(
            subject,
            "".join(f"<li>{key}: {value}</li>" for key, value in self.info.items()),
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
