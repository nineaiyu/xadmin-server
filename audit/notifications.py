#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""audit 域用户/系统消息（自 system/notifications.py 随 audit 域切分迁入）。

消息类型（message_type = 类名）与 category 串不变，既有订阅行与模板零迁移。
"""

from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from common.utils.timezone import local_now_display
from identity.services import get_active_superuser_queryset
from notifications.services import BACKEND, SystemMessage, SystemMsgSubscription, register_message


@register_message
class SensitiveOperationMessage(SystemMessage):
    """敏感操作告警（审计）：命中方法/路径清单的操作，经站内信 + 邮件告知全部超管。"""

    category = "Audit"
    category_label = _("Audit")
    message_type_label = _("Sensitive operation alert")

    def __init__(self, operation: dict):
        self.operation = operation

    def get_html_msg(self) -> dict:
        op = self.operation
        subject = _("Sensitive operation alert: {} {}").format(op.get("method"), op.get("path"))
        message = render_to_string(
            "notify/msg_sensitive_operation.html",
            {
                "module": op.get("module") or "-",
                "method": op.get("method") or "-",
                "path": op.get("path") or "-",
                "ipaddress": op.get("ipaddress") or "-",
                "time": op.get("created_time") or "-",
            },
        )
        return {"subject": subject, "message": message}

    def get_site_msg_msg(self):
        info = self.get_html_msg()
        info["level"] = "danger"
        return info

    @classmethod
    def post_insert_to_db(cls, subscription: SystemMsgSubscription):
        subscription.users.add(*get_active_superuser_queryset())
        subscription.receive_backends = [BACKEND.SITE_MSG, BACKEND.EMAIL]
        subscription.save()

    def publish(self, is_async=False):
        """发布告警；订阅收件人为空时自愈补齐活跃超管。

        存量库可能在超管初始化前就建好订阅（post_migrate 种子时机不保证），
        不自愈会让敏感操作告警永久静默。参照 ServerPerformanceMessage 的做法。
        """
        subscription = SystemMsgSubscription.objects.get(message_type=self.get_message_type())
        if not subscription.users.exists():
            self.post_insert_to_db(subscription)
        super().publish(is_async=is_async)

    @classmethod
    def gen_test_msg(cls):
        return cls(
            {
                "module": _("Operation log"),
                "path": "/api/system/user/1",
                "method": "DELETE",
                "ipaddress": "127.0.0.1",
                "created_time": local_now_display(),
            }
        )
