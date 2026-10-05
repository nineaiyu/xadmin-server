# -*- coding: utf-8 -*-
"""岗位通知（notice_type=POST）：收件范围与计数口径。

岗位为人员维度：按「持有该岗位的用户」展开接收人，且仅启用岗位的在岗用户可见
（与审批人解析同口径，软删除/停用岗位不参与）。
"""

import pytest

from identity.models import Post, UserInfo
from notifications.models import MessageContent
from notifications.views.user_site_msg import get_users_notice_q

pytestmark = pytest.mark.django_db


@pytest.fixture
def holder(db):
    return UserInfo.objects.create_user(username="post_holder", password="Test@123456")


@pytest.fixture
def post(db):
    return Post.objects.create(name="安全员", code="notice_post_security")


def _post_notice(post_obj) -> MessageContent:
    notice = MessageContent.objects.create(title="岗位通知", message="m", notice_type=MessageContent.NoticeChoices.POST)
    notice.notice_post.add(post_obj)
    return notice


class TestPostNoticeScope:
    def test_holder_matches_scope_q(self, holder, post):
        holder.posts.add(post)
        notice = _post_notice(post)
        assert MessageContent.objects.filter(get_users_notice_q(holder)).filter(pk=notice.pk).exists()

    def test_non_holder_not_matched(self, holder, post):
        _post_notice(post)
        assert not MessageContent.objects.filter(get_users_notice_q(holder)).exists()

    def test_inactive_post_excluded(self, holder, post):
        holder.posts.add(post)
        post.is_active = False
        post.save(update_fields=["is_active"])
        assert not MessageContent.objects.filter(get_users_notice_q(holder)).exists()

    def test_soft_deleted_post_excluded(self, holder, post):
        from django.utils import timezone

        holder.posts.add(post)
        post.deleted_at = timezone.now()
        post.save(update_fields=["deleted_at"])
        assert not MessageContent.objects.filter(get_users_notice_q(holder)).exists()


class TestPostNoticeUserCount:
    def test_count_only_active_holders(self, holder, post):
        from notifications.serializers.message import NoticeMessageSerializer

        holder.posts.add(post)
        other = UserInfo.objects.create_user(username="post_holder2", password="Test@123456")
        other.posts.add(post)
        # 非在岗用户不计入
        idle = UserInfo.objects.create_user(username="post_idle", password="Test@123456", is_active=False)
        idle.posts.add(post)

        notice = _post_notice(post)
        count = NoticeMessageSerializer().get_user_count(notice)
        assert count == 2

        # 岗位停用后计数归零（口径与可见性一致）
        post.is_active = False
        post.save(update_fields=["is_active"])
        assert NoticeMessageSerializer().get_user_count(notice) == 0


NOTICE_MSG_URL = "/api/notifications/notice-messages"


class TestPostNoticeValidate:
    """走真实 API（self.request 依赖中间件注入的线程局部请求，纯单测无法构造）。"""

    def test_post_type_requires_notice_post(self, auth_client, superuser):
        resp = auth_client.post(
            NOTICE_MSG_URL,
            {
                "title": "x",
                "message": "m",
                "level": "info",
                "notice_type": MessageContent.NoticeChoices.POST,
                "notice_user": [str(superuser.pk)],
                "files": [],
            },
            format="json",
        )
        assert resp.status_code == 400
        assert not MessageContent.objects.filter(title="x").exists()

    def test_role_type_drops_notice_post(self, auth_client, superuser, post):
        """维度互斥：非 POST 类型的 notice_post 载荷被清理，不会误存。"""
        from identity.models import UserRole

        role = UserRole.objects.create(name="通知角色", code="notice_role_x")
        resp = auth_client.post(
            NOTICE_MSG_URL,
            {
                "title": "角色通知x",
                "message": "m",
                "level": "info",
                "notice_type": MessageContent.NoticeChoices.ROLE,
                "notice_role": [str(role.pk)],
                "notice_user": [str(superuser.pk)],
                "notice_post": [str(post.pk)],
                "files": [],
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        notice = MessageContent.objects.get(pk=resp.data["data"]["pk"])
        assert notice.notice_role.filter(pk=role.pk).exists()
        assert not notice.notice_post.exists()
