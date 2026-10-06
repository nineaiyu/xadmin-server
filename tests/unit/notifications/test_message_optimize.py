# -*- coding: utf-8 -*-
"""消息中心批量化测试。

覆盖：
1. 列表 unread 字段的查询数与页内消息条数解耦；
2. 优化前后响应数据逐字段一致（unread / read_user_count）；
3. read_message 固定 3 条 SQL，与 pks 数量无关；
4. read_message 幂等且结果正确；
5. DEPT/ROLE/POST 目标人数与公告类已读人数的整页聚合（含软删除边界）。
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from identity.models import DeptInfo, Post, UserInfo, UserRole
from notifications.models import MessageContent, MessageUserRead
from notifications.serializers.message import NoticeMessageSerializer, UserNoticeSerializer
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


class TestUnreadSummaryCache:
    """未读汇总短缓存：角标口径（list/unread）命中 30s 缓存，标记已读后主动失效。"""

    UNREAD_URL = f"{SITE_MSG_URL}/unread"

    def test_summary_cached_and_invalidated_on_read(self, auth_client, superuser):
        _make_message(superuser, MessageContent.NoticeChoices.USER, "cache-probe", unread=True)
        first = auth_client.get(self.UNREAD_URL)
        total = first.data["data"]["total"]
        assert total >= 1

        # 新增未读：TTL 内命中缓存（短缓存语义 = 角标最多滞后 TTL）
        _make_message(superuser, MessageContent.NoticeChoices.USER, "cache-probe-2", unread=True)
        assert auth_client.get(self.UNREAD_URL).data["data"]["total"] == total

        # 全部已读 → 主动失效 → 角标立即归零；list 的未读总数同源
        auth_client.patch(f"{SITE_MSG_URL}/all-read")
        assert auth_client.get(self.UNREAD_URL).data["data"]["total"] == 0
        assert auth_client.get(SITE_MSG_URL).data["unread_count"] == 0

    def test_filtered_list_uses_live_count(self, auth_client, superuser):
        """带筛选参数时未读数按当前条件实时统计（不走角标缓存）。"""
        _make_message(superuser, MessageContent.NoticeChoices.USER, "live-1", unread=True)
        _make_message(superuser, MessageContent.NoticeChoices.USER, "live-2", unread=True)
        resp = auth_client.get(SITE_MSG_URL, {"title": "live-1"})
        assert resp.data["unread_count"] == 1


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


def _per_row_target_count_queries(ctx):
    """筛出目标人数/已读人数的逐行 COUNT（含 notice_* 关联表且无 GROUP BY）。

    整页聚合查询带 GROUP BY，逐行回表（回退路径或回归）则没有。
    """
    tables = ("notice_dept", "notice_role", "notice_post", "notice_user")
    return [
        q
        for q in _business_queries(ctx)
        if "COUNT(" in q["sql"].upper()
        and "GROUP BY" not in q["sql"].upper()
        and any(table in q["sql"] for table in tables)
    ]


@pytest.fixture
def multi_type_page(db, normal_user):
    """覆盖五类消息的计数口径：

    - USER：1 名接收人且已读；NOTICE：无接收人（notice_user 口径为 0）；
    - DEPT：目标部门 1 名在册用户，另 1 人属其它部门不计；
    - ROLE：1 名用户持有目标角色（一人一角色，不涉及多角色重复计数）；
    - POST：岗位持有人 2 人，其中停用用户不计。
    """
    dept = DeptInfo.objects.create(name="通知部", code="multi_type_dept_a")
    other_dept = DeptInfo.objects.create(name="无关部", code="multi_type_dept_b")
    role = UserRole.objects.create(name="通知角色", code="multi_type_role_a")
    post = Post.objects.create(name="通知岗", code="multi_type_post_a")

    dept_user = UserInfo.objects.create_user(username="multi_type_dept_user", password="Test@123456")
    dept_user.dept = dept
    dept_user.save(update_fields=["dept"])
    outsider = UserInfo.objects.create_user(username="multi_type_outsider", password="Test@123456")
    outsider.dept = other_dept
    outsider.save(update_fields=["dept"])

    role_user = UserInfo.objects.create_user(username="multi_type_role_user", password="Test@123456")
    role_user.roles.add(role)

    holder = UserInfo.objects.create_user(username="multi_type_post_holder", password="Test@123456")
    holder.posts.add(post)
    idle = UserInfo.objects.create_user(username="multi_type_post_idle", password="Test@123456", is_active=False)
    idle.posts.add(post)

    user_msg = MessageContent.objects.create(
        title="mt-user", message="m", notice_type=MessageContent.NoticeChoices.USER
    )
    user_msg.notice_user.add(normal_user)
    MessageUserRead.objects.filter(owner=normal_user, notice=user_msg).update(unread=False)
    dept_msg = MessageContent.objects.create(
        title="mt-dept", message="m", notice_type=MessageContent.NoticeChoices.DEPT
    )
    dept_msg.notice_dept.add(dept)
    role_msg = MessageContent.objects.create(
        title="mt-role", message="m", notice_type=MessageContent.NoticeChoices.ROLE
    )
    role_msg.notice_role.add(role)
    post_msg = MessageContent.objects.create(
        title="mt-post", message="m", notice_type=MessageContent.NoticeChoices.POST
    )
    post_msg.notice_post.add(post)
    notice_msg = MessageContent.objects.create(
        title="mt-notice", message="m", notice_type=MessageContent.NoticeChoices.NOTICE
    )

    return {
        "user": user_msg,
        "dept": dept_msg,
        "role": role_msg,
        "post": post_msg,
        "notice": notice_msg,
        "role_obj": role,
        "post_obj": post,
        "normal_user": normal_user,
    }


class TestMultiTypeCountBatching:
    """DEPT/ROLE/POST 目标人数与公告类已读人数的整页聚合及口径边界。"""

    def test_counts_values_match(self, auth_client, multi_type_page):
        resp = auth_client.get(NOTICE_MSG_URL, {"page": 1, "size": 50, "page_size": 50})
        assert resp.status_code == 200
        results = {item["title"]: item for item in resp.data["data"]["results"]}

        assert results["mt-user"]["user_count"] == 1
        assert results["mt-user"]["read_user_count"] == 1
        assert results["mt-dept"]["user_count"] == 1
        assert results["mt-role"]["user_count"] == 1
        assert results["mt-post"]["user_count"] == 1
        # 公告类 read_user_count 口径即 notice_user 关联数：不直填接收人时为 0
        assert results["mt-dept"]["read_user_count"] == 0
        assert results["mt-role"]["read_user_count"] == 0
        assert results["mt-post"]["read_user_count"] == 0
        assert results["mt-notice"]["user_count"] == 0
        assert results["mt-notice"]["read_user_count"] == 0

    def test_counts_match_per_object_semantics(self, auth_client, multi_type_page):
        """整页聚合结果与逐对象查询（单对象序列化路径）逐条一致。"""
        resp = auth_client.get(NOTICE_MSG_URL, {"page": 1, "size": 50, "page_size": 50})
        results = {item["title"]: item for item in resp.data["data"]["results"]}

        for notice in (
            multi_type_page["user"],
            multi_type_page["dept"],
            multi_type_page["role"],
            multi_type_page["post"],
            multi_type_page["notice"],
        ):
            serializer = NoticeMessageSerializer()
            assert results[notice.title]["user_count"] == serializer.get_user_count(notice), notice.title
            assert results[notice.title]["read_user_count"] == serializer.get_read_user_count(notice), notice.title

    def test_counts_not_queried_per_row(self, auth_client, multi_type_page):
        with CaptureQueriesContext(connection) as ctx:
            resp = auth_client.get(NOTICE_MSG_URL, {"page": 1, "size": 50, "page_size": 50})
        assert resp.status_code == 200
        assert _per_row_target_count_queries(ctx) == []

    def test_soft_deleted_scope_entities_excluded(self, auth_client, multi_type_page):
        """软删除的角色/岗位/接收人与逐对象口径一致地不计入（join 侧显式过滤）。"""
        page = multi_type_page
        resp = auth_client.get(NOTICE_MSG_URL, {"page": 1, "size": 50, "page_size": 50})
        results = {item["title"]: item for item in resp.data["data"]["results"]}
        assert results["mt-role"]["user_count"] == 1
        assert results["mt-post"]["user_count"] == 1
        assert results["mt-user"]["read_user_count"] == 1

        page["role_obj"].deleted_at = timezone.now()
        page["role_obj"].save(update_fields=["deleted_at"])
        page["post_obj"].deleted_at = timezone.now()
        page["post_obj"].save(update_fields=["deleted_at"])
        page["normal_user"].deleted_at = timezone.now()
        page["normal_user"].save(update_fields=["deleted_at"])

        resp = auth_client.get(NOTICE_MSG_URL, {"page": 1, "size": 50, "page_size": 50})
        results = {item["title"]: item for item in resp.data["data"]["results"]}
        assert results["mt-role"]["user_count"] == 0
        assert results["mt-post"]["user_count"] == 0
        assert results["mt-user"]["read_user_count"] == 0

    def test_recycle_list_keeps_counts(self, auth_client, multi_type_page):
        """回收站列表的软删除消息计数不归零（与逐对象查询同口径）。"""
        multi_type_page["dept"].delete()

        resp = auth_client.get(f"{NOTICE_MSG_URL}/recycle")
        assert resp.status_code == 200
        results = {item["title"]: item for item in resp.data["data"]["results"]}
        assert results["mt-dept"]["user_count"] == 1
