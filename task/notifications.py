#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""task 域用户/系统消息（自 system/notifications.py 随 task 域切分迁入）。

message_type（=类名）与 category 串不变，既有订阅行与模板零迁移。
"""

from django.utils.translation import gettext_lazy as _

from identity.services import get_active_superuser_queryset
from notifications.services import BACKEND, SystemMessage, SystemMsgSubscription, register_message


@register_message
class WebhookFailedMessage(SystemMessage):
    """Webhook 投递耗尽告警：站内信告知全部超管。"""

    category = "Audit"
    category_label = _("Audit")
    message_type_label = _("Webhook delivery exhausted")

    def __init__(self, info: dict):
        self.info = info

    def get_html_msg(self) -> dict:
        info = self.info
        subject = _("Webhook delivery exhausted: {}").format(info.get("subscription"))
        message = "<p>{}</p><ul><li>event: {}</li><li>attempts: {}</li><li>error: {}</li></ul>".format(
            subject, info.get("event"), info.get("attempts"), info.get("error")
        )
        return {"subject": subject, "message": message}

    def get_site_msg_msg(self):
        info = self.get_html_msg()
        info["level"] = "danger"
        return info

    @classmethod
    def post_insert_to_db(cls, subscription: SystemMsgSubscription):
        subscription.users.add(*get_active_superuser_queryset())
        subscription.receive_backends = [BACKEND.SITE_MSG]
        subscription.save()

    def publish(self, is_async=False):
        """发布告警；订阅收件人为空时自愈补齐活跃超管（post_migrate 种子早于建号）。"""
        subscription = SystemMsgSubscription.objects.get(message_type=self.get_message_type())
        if not subscription.users.exists():
            self.post_insert_to_db(subscription)
        super().publish(is_async=is_async)

    @classmethod
    def gen_test_msg(cls):
        return cls({"subscription": "demo", "event": "user.login_failed", "attempts": 5, "error": "timeout"})
