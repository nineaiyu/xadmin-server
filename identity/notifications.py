#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""identity 域用户消息：账号安全提醒 / LDAP 同步摘要 / 开放平台配额告警。

自 system/notifications.py 按消息域拆出；message_type（=类名）与 category 串
不变，既有订阅行与消息模板零迁移。敏感操作告警随 audit 域、Webhook 投递告警
随 task 域、审批通知随 approval 域各自归位。
"""

from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from common.utils.request import get_browser, get_request_ip
from common.utils.timezone import local_now_display
from identity.services import get_active_superuser_queryset
from notifications.services import BACKEND, SystemMessage, SystemMsgSubscription, UserMessage, register_message

__all__ = [
    "DifferentCityLoginMessage",
    "AbnormalLoginMessage",
    "ResetPasswordSuccessMsg",
    "LdapSyncMessage",
    "ApiQuotaWarningMessage",
]


@register_message
class DifferentCityLoginMessage(UserMessage):
    category = "AccountSecurity"
    category_label = _("Account Security")
    message_type_label = _("Different city login reminder")

    def __init__(self, user, ip, city):
        self.ip = ip
        self.city = city
        super().__init__(user)

    def get_html_msg(self) -> dict:
        now = local_now_display()
        subject = _("Different city login reminder")
        context = dict(
            subject=subject,
            name=self.user_display,
            username=self.user_username,
            ip=self.ip,
            time=now,
            city=self.city,
        )
        message = render_to_string("notify/msg_different_city.html", context)
        return {"subject": subject, "message": message}

    @classmethod
    def gen_test_msg(cls):
        from identity.models import UserInfo

        user = UserInfo.objects.first()
        ip = "8.8.8.8"
        city = "洛杉矶"
        return cls(user, ip, city)


@register_message
class AbnormalLoginMessage(UserMessage):
    """新设备/新 IP 登录提醒（异常登录第二维度，与异地城市提醒互补）。"""

    category = "AccountSecurity"
    category_label = _("Account Security")
    message_type_label = _("New device login reminder")

    def __init__(self, user, dimensions, info):
        # dimensions: 新维度清单（如 ["ip", "device"]）
        self.dimensions = dimensions
        self.info = info
        super().__init__(user)

    def get_html_msg(self) -> dict:
        subject = _("New device login reminder")
        dimension_texts = {
            "ip": _("New IP address"),
            "city": _("New city"),
            "device": _("New device (browser/system)"),
        }
        info = self.info or {}
        context = dict(
            subject=subject,
            name=self.user_display,
            username=self.user_username,
            # 维度清单在 Python 侧翻译好后传入模板，模板不再做带参数的翻译
            dimensions=[dimension_texts.get(d, d) for d in self.dimensions],
            ip=info.get("ip") or "-",
            city=info.get("city") or "-",
            browser=info.get("browser") or "-",
            system=info.get("system") or "-",
            time=info.get("time") or "-",
        )
        message = render_to_string("notify/msg_abnormal_login.html", context)
        return {"subject": subject, "message": message}

    @classmethod
    def gen_test_msg(cls):
        from identity.models import UserInfo

        user = UserInfo.objects.first()
        return cls(
            user,
            ["ip", "device"],
            {"ip": "8.8.8.8", "browser": "Chrome", "system": "macOS", "time": local_now_display()},
        )


@register_message
class ResetPasswordSuccessMsg(UserMessage):
    category = "AccountSecurity"
    category_label = _("Account Security")
    message_type_label = _("Reset password reminder")

    def __init__(self, user, request):
        super().__init__(user)
        self.ip_address = get_request_ip(request)
        self.browser = get_browser(request)

    def get_html_msg(self) -> dict:
        user = self.user

        subject = _("Reset password success")
        context = {
            "name": user.nickname,
            "username": user.username,
            "ip_address": self.ip_address,
            "browser": self.browser,
        }
        message = render_to_string("notify/msg_rest_password_success.html", context)
        return {"subject": subject, "message": message}

    @classmethod
    def gen_test_msg(cls):
        # 无可安全构造的测试场景（真实改密事件触发）：显式返回 None，测试消息端点对 None noop
        return None


@register_message
class LdapSyncMessage(SystemMessage):
    """LDAP 目录同步摘要：有建号/处置/冲突动作时告知全部超管。"""

    category = "Audit"
    category_label = _("Audit")
    message_type_label = _("LDAP sync summary")

    def __init__(self, summary: dict):
        self.summary = summary

    def get_html_msg(self) -> dict:
        subject = _("LDAP sync finished")
        lines = "".join(f"<li>{key}: {value}</li>" for key, value in self.summary.items())
        message = f"<p>{subject}</p><ul>{lines}</ul>"
        return {"subject": subject, "message": message}

    def get_site_msg_msg(self):
        info = self.get_html_msg()
        info["level"] = "info"
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
        return cls({"updated_users": 1})


@register_message
class ApiQuotaWarningMessage(SystemMessage):
    """API 应用每日配额软告警：达阈值当日首次越线，站内信告知全部超管。"""

    category = "Audit"
    category_label = _("Audit")
    message_type_label = _("API application quota warning")

    def __init__(self, info: dict):
        self.info = info

    def get_html_msg(self) -> dict:
        info = self.info
        subject = _("API application quota warning: {}").format(info.get("application"))
        message = "<p>{}</p><ul><li>client_id: {}</li><li>used: {}</li><li>quota: {}</li></ul>".format(
            subject, info.get("client_id"), info.get("used"), info.get("quota")
        )
        return {"subject": subject, "message": message}

    def get_site_msg_msg(self):
        info = self.get_html_msg()
        info["level"] = "warning"
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
        return cls({"application": "演示应用", "client_id": "app_demo", "used": 82, "quota": 100})
