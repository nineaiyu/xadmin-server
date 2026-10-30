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
    def test_post_notice_pushes_holders(self, monkeypatch, holder, post):
        """发布岗位通知：post_save/m2m 同口径展开到岗位成员并触发实时推送。"""
        from notifications.message import SiteMessageUtil

        holder.posts.add(post)
        pushed = []
        monkeypatch.setattr(
            SiteMessageUtil,
            "push_notice_messages",
            lambda notice, pks: pushed.append(set(pks)),
        )
        notice = MessageContent.objects.create(
            title="岗位推送", message="m", notice_type=MessageContent.NoticeChoices.POST, publish=True
        )
        notice.notice_post.add(post)

        assert pushed and pushed[-1] == {holder.pk}

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


class TestRoleNoticeScope:
    def test_dept_role_holder_matches_scope_q(self):
        """角色通知的可见域与角色解析同口径：部门挂角色用户也应命中。"""
        from identity.models import DeptInfo, UserRole

        role = UserRole.objects.create(name="部门通知角色", code="notice_dept_role")
        dept = DeptInfo.objects.create(name="通知部", code="notice_dept")
        dept.roles.add(role)
        user = UserInfo.objects.create_user(username="dept_role_holder", password="Test@123456", dept=dept)
        notice = MessageContent.objects.create(
            title="部门角色通知", message="m", notice_type=MessageContent.NoticeChoices.ROLE
        )
        notice.notice_role.add(role)

        assert MessageContent.objects.filter(get_users_notice_q(user)).filter(pk=notice.pk).exists()

    def test_dept_role_holder_receives_push(self, monkeypatch):
        """发布角色通知：部门挂角色用户与直挂用户同口径进入实时推送。"""
        from identity.models import DeptInfo, UserRole
        from notifications.message import SiteMessageUtil

        role = UserRole.objects.create(name="推送部门角色", code="push_dept_role")
        dept = DeptInfo.objects.create(name="推送部", code="push_dept")
        dept.roles.add(role)
        holder = UserInfo.objects.create_user(username="push_dept_holder", password="Test@123456", dept=dept)
        pushed = []
        monkeypatch.setattr(
            SiteMessageUtil,
            "push_notice_messages",
            lambda notice, pks: pushed.append(set(pks)),
        )
        notice = MessageContent.objects.create(
            title="角色推送", message="m", notice_type=MessageContent.NoticeChoices.ROLE, publish=True
        )
        notice.notice_role.add(role)

        assert pushed and holder.pk in pushed[-1]

    def test_user_count_includes_dept_role_members(self):
        """目标人数与可见域同口径：部门挂角色的成员也计入。"""
        from identity.models import DeptInfo, UserRole
        from notifications.serializers.message import NoticeMessageSerializer

        role = UserRole.objects.create(name="计数部门角色", code="count_dept_role")
        dept = DeptInfo.objects.create(name="计数部", code="count_dept")
        dept.roles.add(role)
        UserInfo.objects.create_user(username="count_dept_holder", password="Test@123456", dept=dept)
        direct = UserInfo.objects.create_user(username="count_direct_holder", password="Test@123456")
        direct.roles.add(role)
        notice = MessageContent.objects.create(
            title="角色计数", message="m", notice_type=MessageContent.NoticeChoices.ROLE
        )
        notice.notice_role.add(role)

        assert NoticeMessageSerializer().get_user_count(notice) == 2
        # 整页批量口径（一次聚合）同样包含部门挂角色成员
        page = NoticeMessageSerializer([notice], many=True).data
        assert page[0]["user_count"] == 2

    def test_inactive_role_count_is_zero(self):
        """停用/软删角色不再产生可见接收人，目标人数同步归零。"""
        from identity.models import UserRole
        from notifications.serializers.message import NoticeMessageSerializer

        role = UserRole.objects.create(name="停用角色", code="count_inactive_role")
        user = UserInfo.objects.create_user(username="inactive_role_holder", password="Test@123456")
        user.roles.add(role)
        notice = MessageContent.objects.create(
            title="停用角色计数", message="m", notice_type=MessageContent.NoticeChoices.ROLE
        )
        notice.notice_role.add(role)
        assert NoticeMessageSerializer().get_user_count(notice) == 1

        role.is_active = False
        role.save(update_fields=["is_active"])
        assert NoticeMessageSerializer().get_user_count(notice) == 0


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
