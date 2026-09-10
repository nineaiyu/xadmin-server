# -*- coding: utf-8 -*-
"""消息中心批量化测试。

覆盖：
1. 列表 unread 字段的查询数与页内消息条数解耦；
2. 优化前后响应数据逐字段一致（unread / read_user_count）；
3. read_message 固定 3 条 SQL，与 pks 数量无关；
4. read_message 幂等且结果正确。
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from notifications.models import MessageContent, MessageUserRead
from notifications.serializers.message import UserNoticeSerializer
from notifications.views.user_site_msg import UserSiteMessageViewSet

pytestmark = pytest.mark.django_db

SITE_MSG_URL = "/api/notifications/site-messages"


def _business_queries(ctx):
    return [q for q in ctx.captured_queries if "SAVEPOINT" not in q["sql"]]


def _make_message(user, notice_type, title, unread=False, publish=True):
    msg = MessageContent.objects.create(title=title, message="m", notice_type=notice_type, publish=publish)
    if notice_type in MessageContent.get_user_choices():
        msg.notice_user.add(user)  # through 表默认 unread=True
        MessageUserRead.objects.filter(owner=user, notice=msg).update(unread=unread)
    return msg


@pytest.fixture
def message_page(db, normal_user):
    """6 条用户通知：3 未读、3 已读；外加 1 条公告"""
    messages = []
    for i in range(6):
        messages.append(
            _make_message(normal_user, MessageContent.NoticeChoices.USER, f"user-msg-{i}", unread=(i % 2 == 0))
        )
    notice = MessageContent.objects.create(
        title="notice-0", message="m", notice_type=MessageContent.NoticeChoices.NOTICE
    )
    messages.append(notice)
    return messages


class TestUnreadBatching:
    def test_query_count_independent_of_page_size(self, auth_client, message_page):
        with CaptureQueriesContext(connection) as ctx_small:
            resp_small = auth_client.get(SITE_MSG_URL, {"page": 1, "size": 2, "page_size": 2})
        assert resp_small.status_code == 200

        with CaptureQueriesContext(connection) as ctx_large:
            resp_large = auth_client.get(SITE_MSG_URL, {"page": 1, "size": 50, "page_size": 50})
        assert resp_large.status_code == 200

        small = len(_business_queries(ctx_small))
        large = len(_business_queries(ctx_large))
        assert small > 0
        # 页内消息数从 2 变为 6，查询数不随行数线性增长
        assert large <= small + 2, f"small={small} large={large}"

    def test_unread_values_match_expected(self, normal_user, message_page):
        """逐对象序列化结果与预期一致：偶数下标未读、奇数下标已读、公告未读"""
        context = {"request": type("R", (), {"user": normal_user})()}
        data = UserNoticeSerializer(message_page, many=True, context=context).data

        unread_by_title = {item["title"]: item["unread"] for item in data}
        for i in range(6):
            assert unread_by_title[f"user-msg-{i}"] is (i % 2 == 0), unread_by_title
        assert unread_by_title["notice-0"] is True

    def test_announcement_becomes_read_after_read_row_created(self, normal_user):
        notice = MessageContent.objects.create(title="n", message="m", notice_type=MessageContent.NoticeChoices.NOTICE)
        context = {"request": type("R", (), {"user": normal_user})()}
        assert UserNoticeSerializer(notice, context=context).data["unread"] is True

        MessageUserRead.objects.create(owner=normal_user, notice=notice, unread=False)
        context = {"request": type("R", (), {"user": normal_user})()}
        assert UserNoticeSerializer(notice, context=context).data["unread"] is False


class TestReadMessage:
    def test_fixed_three_queries(self, api_client, normal_user, message_page):
        pks = [m.pk for m in message_page]
        view = UserSiteMessageViewSet()

        class FakeRequest:
            user = normal_user

        with CaptureQueriesContext(connection) as ctx:
            resp = view.read_message(pks, FakeRequest())
        assert resp.status_code == 200

        assert len(_business_queries(ctx)) <= 3

    def test_marks_all_as_read(self, api_client, normal_user, message_page):
        pks = [m.pk for m in message_page]
        view = UserSiteMessageViewSet()

        class FakeRequest:
            user = normal_user

        view.read_message(pks, FakeRequest())

        assert MessageUserRead.objects.filter(owner=normal_user, unread=True).count() == 0
        assert MessageUserRead.objects.filter(owner=normal_user, unread=False).count() == len(pks)

    def test_idempotent_and_deduplicates(self, normal_user):
        msg = _make_message(normal_user, MessageContent.NoticeChoices.USER, "idem")
        view = UserSiteMessageViewSet()

        class FakeRequest:
            user = normal_user

        pks = [msg.pk, msg.pk]
        view.read_message(pks, FakeRequest())
        view.read_message(pks, FakeRequest())

        assert MessageUserRead.objects.filter(owner=normal_user).count() == 1
        assert MessageUserRead.objects.get(owner=normal_user).unread is False

    def test_empty_pks_noop(self, normal_user):
        view = UserSiteMessageViewSet()

        class FakeRequest:
            user = normal_user

        resp = view.read_message([], FakeRequest())
        assert resp.status_code == 200
        assert MessageUserRead.objects.count() == 0


NOTICE_MSG_URL = "/api/notifications/notice-messages"


def _per_row_user_count_queries(ctx):
    """筛出 user_count 的逐行 COUNT(notice_user)。

    排除整页聚合查询（带 GROUP BY），后者是本次优化期望出现的唯一一次统计。
    """
    return [
        q
        for q in _business_queries(ctx)
        if "COUNT(*)" in q["sql"].upper() and "notice_user" in q["sql"] and "GROUP BY" not in q["sql"].upper()
    ]


@pytest.fixture
def notice_page(db, normal_user):
    """5 条用户通知（各 1 个接收人）+ 1 条公告（无接收人）"""
    messages = []
    for i in range(5):
        msg = MessageContent.objects.create(title=f"n-{i}", message="m", notice_type=MessageContent.NoticeChoices.USER)
        msg.notice_user.add(normal_user)
        messages.append(msg)
    messages.append(
        MessageContent.objects.create(title="notice", message="m", notice_type=MessageContent.NoticeChoices.NOTICE)
    )
    return messages


class TestNoticeMessageCountBatching:
    """消息通知管理列表的 user_count / read_user_count 整页聚合。

    回归点：`get_page_instances()` 不传参会恒返回 []（依赖 default 的类型判断
    当前是否整页序列化），一旦漏传，批量化会静默失效退回逐行 COUNT。
    """

    def test_user_count_not_queried_per_row(self, auth_client, notice_page):
        with CaptureQueriesContext(connection) as ctx:
            resp = auth_client.get(NOTICE_MSG_URL, {"page": 1, "size": 50, "page_size": 50})
        assert resp.status_code == 200
        per_row = _per_row_user_count_queries(ctx)
        assert per_row == [], per_row

    def test_user_count_values_match(self, auth_client, notice_page):
        resp = auth_client.get(NOTICE_MSG_URL, {"page": 1, "size": 50, "page_size": 50})
        results = {item["title"]: item for item in resp.data["data"]["results"]}

        for i in range(5):
            assert results[f"n-{i}"]["user_count"] == 1
        # 公告类接收人由 notice_user 表达，此处未添加接收人
        assert results["notice"]["user_count"] == 0

    def test_read_user_count_values_match(self, auth_client, notice_page, normal_user):
        first = notice_page[0]
        # notice_user 的 through 表即 MessageUserRead：add 已建行，这里改为已读
        MessageUserRead.objects.filter(owner=normal_user, notice=first).update(unread=False)

        resp = auth_client.get(NOTICE_MSG_URL, {"page": 1, "size": 50, "page_size": 50})
        results = {item["title"]: item for item in resp.data["data"]["results"]}

        assert results["n-0"]["read_user_count"] == 1
        assert results["n-1"]["read_user_count"] == 0
