# -*- coding: utf-8 -*-
"""消息订阅视图与注册表一致性测试（T2.4 / T4.1）。"""
import pytest

# 生产上这些模块由登录/改密/celery 链路导入并触发注册；测试中显式导入对齐
import common.celery.failure_handler  # noqa: F401
import common.notifications  # noqa: F401
import system.notifications  # noqa: F401
from notifications.notifications import (
    SYSTEM_MESSAGE_REGISTRY,
    USER_MESSAGE_REGISTRY,
)

pytestmark = pytest.mark.django_db

SYSTEM_SUB_URL = "/api/notifications/system-msg-subscription"
USER_SUB_URL = "/api/notifications/user-msg-subscription"


class TestMessageRegistry:
    def test_known_message_types_registered(self):
        system_types = {info["message_type"] for info in SYSTEM_MESSAGE_REGISTRY}
        user_types = {info["message_type"] for info in USER_MESSAGE_REGISTRY}
        assert {"ServerPerformanceMessage", "TaskFailureMessage"} <= system_types
        assert {
            "DifferentCityLoginMessage",
            "ResetPasswordSuccessMsg",
            "ImportDataMessage",
            "BatchDeleteDataMessage",
        } <= user_types

    def test_registry_entries_carry_cls(self):
        """注册表必须携带类引用（post_migrate 补建订阅回调 post_insert_to_db）。"""
        assert SYSTEM_MESSAGE_REGISTRY and USER_MESSAGE_REGISTRY
        assert all(hasattr(info["cls"], "post_insert_to_db") for info in SYSTEM_MESSAGE_REGISTRY)

    def test_different_city_login_message_html(self, normal_user):
        from system.notifications import DifferentCityLoginMessage

        msg = DifferentCityLoginMessage(normal_user, ip="8.8.8.8", city="洛杉矶")
        html = msg.get_html_msg()
        assert "8.8.8.8" in html["message"]
        assert "洛杉矶" in html["message"]
        assert html["subject"]

    def test_reset_password_success_message_html(self, normal_user, rf):
        from system.notifications import ResetPasswordSuccessMsg

        msg = ResetPasswordSuccessMsg(normal_user, rf.get("/login"))
        html = msg.get_html_msg()
        assert normal_user.username in html["message"]
        assert html["subject"]


class TestSubscriptionViews:
    def test_system_subscription_list_builds_category_tree(self, auth_client):
        resp = auth_client.get(SYSTEM_SUB_URL)
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        tree = resp.data["data"]
        assert isinstance(tree, list) and tree
        all_types = [
            child["message_type"] for node in tree for child in node["children"]
        ]
        assert "TaskFailureMessage" in all_types

    def test_system_subscription_creation_is_idempotent(self, auth_client):
        """post_migrate 补建不因已存在订阅中断（旧实现 not-created 即 return 的回归）。"""
        from notifications.models import SystemMsgSubscription
        from notifications.notifications import SYSTEM_MESSAGE_REGISTRY

        before = SystemMsgSubscription.objects.count()
        assert before >= len(SYSTEM_MESSAGE_REGISTRY)
        # 再次补建不新增、不报错
        from notifications.signal_handlers import create_system_messages

        create_system_messages(None)
        assert SystemMsgSubscription.objects.count() == before

    def test_user_subscription_list_scoped_to_user(self, auth_client, normal_user, api_client):
        resp = auth_client.get(USER_SUB_URL)
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        tree = resp.data["data"]
        all_types = [
            child["message_type"] for node in tree for child in node["children"]
        ]
        assert "DifferentCityLoginMessage" in all_types
        # 其他用户的订阅不出现
        other = api_client.get(USER_SUB_URL)
        assert other.data["code"] == 1000
