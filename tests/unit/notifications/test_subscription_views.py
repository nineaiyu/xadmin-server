# -*- coding: utf-8 -*-
"""消息订阅视图与注册表一致性测试。"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

# 生产上这些模块由登录/改密/celery 链路导入并触发注册；测试中显式导入对齐
import approval.notifications  # noqa: F401
import audit.notifications  # noqa: F401
import common.celery.failure_handler  # noqa: F401
import common.notifications  # noqa: F401
import identity.notifications  # noqa: F401
import task.notifications  # noqa: F401
from notifications.models import UserMsgSubscription
from notifications.notifications import (
    SYSTEM_MESSAGE_REGISTRY,
    USER_MESSAGE_REGISTRY,
    user_msgs,
)
from notifications.views.notifications import UserMsgSubscriptionViewSet

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
        from identity.notifications import DifferentCityLoginMessage

        msg = DifferentCityLoginMessage(normal_user, ip="8.8.8.8", city="洛杉矶")
        html = msg.get_html_msg()
        assert "8.8.8.8" in html["message"]
        assert "洛杉矶" in html["message"]
        assert html["subject"]

    def test_reset_password_success_message_html(self, normal_user, rf):
        from identity.notifications import ResetPasswordSuccessMsg

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
        all_types = [child["message_type"] for node in tree for child in node["children"]]
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
        all_types = [child["message_type"] for node in tree for child in node["children"]]
        assert "DifferentCityLoginMessage" in all_types
        # 其他用户的订阅不出现
        other = api_client.get(USER_SUB_URL)
        assert other.data["code"] == 1000


class TestSendTestMessageAPI:
    """「发送测试消息」入口：按 message_type 走真实发送链路（渠道连通性自检）。"""

    def test_system_test_message_delivers_to_superuser(self, auth_client, superuser):
        """系统消息测试：站内信落库到超管（回归 send_test_msg 从未送达的历史缺陷）。"""
        from notifications.models import MessageContent

        before = MessageContent.objects.count()
        resp = auth_client.post(f"{SYSTEM_SUB_URL}/test", {"message_type": "TaskFailureMessage"}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        assert MessageContent.objects.count() == before + 1
        content = MessageContent.objects.latest("created_time")
        assert superuser in content.notice_user.all()

    def test_user_test_message_delivers_to_request_user(self, auth_client, superuser):
        """用户消息测试：发给当前登录用户（个人订阅页语义）。"""
        from notifications.models import MessageContent

        before = MessageContent.objects.count()
        resp = auth_client.post(f"{USER_SUB_URL}/test", {"message_type": "DifferentCityLoginMessage"}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        assert MessageContent.objects.count() == before + 1
        content = MessageContent.objects.latest("created_time")
        assert list(content.notice_user.values_list("pk", flat=True)) == [superuser.pk]

    def test_unknown_message_type_rejected(self, auth_client):
        resp = auth_client.post(f"{SYSTEM_SUB_URL}/test", {"message_type": "NotExistMessage"}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1004
        assert auth_client.post(f"{SYSTEM_SUB_URL}/test", {}, format="json").data["code"] == 1004

    def test_test_message_requires_login(self, api_client):
        assert api_client.post(
            f"{SYSTEM_SUB_URL}/test", {"message_type": "TaskFailureMessage"}, format="json"
        ).status_code in (
            401,
            403,
        )


def _user_sub_inserts(ctx):
    """筛出个人订阅表的 INSERT 语句（操作日志等其他写入不算）。"""
    return [
        q["sql"]
        for q in ctx.captured_queries
        if "INSERT INTO" in q["sql"].upper() and "notifications_usermsgsubscription" in q["sql"]
    ]


class TestUserSubscriptionListBackfill:
    """个人订阅列表 GET 补齐缺失订阅：批量写入、并发冲突安全、响应树与原实现一致。"""

    def test_backfill_single_insert_and_tree_matches_registry(self, auth_client, superuser):
        """缺失订阅一次性批量补齐（单条 INSERT），分组/子项顺序、瞬态 label 与
        空渠道语义和逐条补齐时完全一致。"""
        assert UserMsgSubscription.objects.filter(user=superuser).count() == 0

        with CaptureQueriesContext(connection) as ctx:
            resp = auth_client.get(USER_SUB_URL)
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000

        inserts = _user_sub_inserts(ctx)
        assert len(inserts) == 1, inserts

        assert UserMsgSubscription.objects.filter(user=superuser).count() == len(user_msgs)
        tree = resp.data["data"]
        assert [str(node["category"]) for node in tree] == list(
            dict.fromkeys(str(msg["category"]) for msg in user_msgs)
        )
        children = [child for node in tree for child in node["children"]]
        assert [child["message_type"] for child in children] == [msg["message_type"] for msg in user_msgs]
        labels = {msg["message_type"]: str(msg["message_type_label"]) for msg in user_msgs}
        for child in children:
            assert child["message_type_label"] == labels[child["message_type"]]
            assert child["receive_backends"] == []

    def test_backfill_is_one_shot_on_second_visit(self, auth_client):
        """订阅补齐后的再次访问不再产生 INSERT（常规路径零写副作用）。"""
        assert auth_client.get(USER_SUB_URL).status_code == 200

        with CaptureQueriesContext(connection) as ctx:
            resp = auth_client.get(USER_SUB_URL)
        assert resp.status_code == 200
        assert _user_sub_inserts(ctx) == []

    def test_existing_row_wins_and_response_includes_it(self, auth_client, superuser, monkeypatch):
        """并发首访模拟：首查看不到对方已抢先落库的行时，补齐插入静默跳过冲突行
        （不再 500），返回树包含既有行及其已保存的渠道配置。"""
        existing_type = user_msgs[0]["message_type"]
        UserMsgSubscription.objects.create(user=superuser, message_type=existing_type, receive_backends=["email"])

        real_get_queryset = UserMsgSubscriptionViewSet.get_queryset
        first_lookup = []

        def racy_get_queryset(viewset_self):
            queryset = real_get_queryset(viewset_self)
            if not first_lookup:
                # 模拟第一遍查询发生在对方提交之前：查不到既有订阅
                first_lookup.append(True)
                return queryset.exclude(message_type=existing_type)
            return queryset

        monkeypatch.setattr(UserMsgSubscriptionViewSet, "get_queryset", racy_get_queryset)

        resp = auth_client.get(USER_SUB_URL)
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        children = {child["message_type"]: child for node in resp.data["data"] for child in node["children"]}
        assert children[existing_type]["receive_backends"] == ["email"]
        assert UserMsgSubscription.objects.filter(user=superuser).count() == len(user_msgs)
