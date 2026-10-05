#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""platform 域用户消息（自 system/notifications.py 按消息域拆分的留存面）。

账号安全/LDAP/配额五类消息已随 identity 域拆分（identity/notifications.py）；
敏感操作告警随 audit 域、Webhook 投递告警随 task 域、审批通知随 approval 域
各自归位。message_type（=类名）与 category 串不变，既有订阅行与模板零迁移。
"""

from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from common.utils.timezone import local_now_display
from identity.services import get_active_superuser_queryset
from notifications.services import BACKEND, SystemMessage, SystemMsgSubscription, UserMessage, register_message

# ApprovalFlowMessage（流程审批通知）在独立模块；此 re-export 维持既有导入面
from system.notifications_approval_flow import ApprovalFlowMessage  # noqa: F401 显式再导出

# 实现拆至 system.notifications_alert：经模块级 __getattr__ 延迟再导出（保持调用面，避免循环导入）。
_MOVED_EXPORTS = ("SENSITIVE_ALERT_THROTTLE_SECONDS", "maybe_alert_sensitive_operation")


def __getattr__(name):
    if name in _MOVED_EXPORTS:
        from importlib import import_module

        return getattr(import_module("system.notifications_alert"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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


@register_message
class ApprovalRequestMessage(UserMessage):
    """审批中心通知：提交（发审批人）/ 通过、驳回（发申请人）三种文案。"""

    category = "Audit"
    category_label = _("Audit")
    message_type_label = _("Approval request notice")

    EVENT_TITLES = {
        "submitted": _("New approval request"),
        "approved": _("Approval request approved"),
        "rejected": _("Approval request rejected"),
        # 超时未处理提醒（每日任务补发一次，见 system.utils.approval.remind_pending_approvals）
        "remind": _("Approval request pending reminder"),
    }

    def __init__(self, user, event: str, approval):
        self.event = event
        self.approval = approval
        super().__init__(user)

    def get_html_msg(self) -> dict:
        approval = self.approval
        subject = self.EVENT_TITLES.get(self.event, self.EVENT_TITLES["submitted"])
        context = dict(
            subject=subject,
            name=self.user_display,
            event=self.event,
            module=approval.module or "-",
            method=approval.method or "-",
            path=approval.path or "-",
            approval_no=str(approval.pk)[:8].upper(),
            reason=approval.reason or "",
            time=local_now_display(),
        )
        message = render_to_string("notify/msg_approval.html", context)
        return {"subject": subject, "message": message}

    @classmethod
    def gen_test_msg(cls):
        from approval.models import ApprovalRequest
        from identity.models import UserInfo

        user = UserInfo.objects.first()
        approval = ApprovalRequest(module="User", method="DELETE", path="/api/system/user/1", creator=user)
        return cls(user, "submitted", approval)
