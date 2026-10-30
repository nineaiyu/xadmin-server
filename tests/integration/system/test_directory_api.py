# -*- coding: utf-8 -*-
"""通讯录 API：菜单权限门 + 名录可见性（数据权限随调用者）+ 部门/岗位/关键字筛选。"""

import uuid

import pytest

from identity.models import DeptInfo, Post, UserInfo

pytestmark = pytest.mark.django_db

DIRECTORY_URL = "/api/identity/directory"


@pytest.fixture
def dept(db):
    return DeptInfo.objects.create(name="研发部", code="dir_dev")


@pytest.fixture
def member(dept):
    return UserInfo.objects.create_user(username="dir_member", password="Test@123456", nickname="名录成员", dept=dept)


def grant(role, menu_factory, name, path, method):
    """授权菜单权限点：同名复用（menu.name 全局唯一）。"""
    from system.models import Menu

    perm = Menu.objects.filter(name=name).first() or menu_factory(name, path=path, method=method)
    role.menu.add(perm)
    return perm


class TestDirectoryPermission:
    def test_requires_permission(self, api_client, normal_user):
        """无 list:SystemDirectory 权限点 → 403（与其他菜单权限同口径，fail-closed）。"""
        api_client.force_authenticate(user=normal_user)
        assert api_client.get(DIRECTORY_URL).status_code == 403

    def test_allowed_with_permission(self, api_client, normal_user, role, menu_factory, member):
        """菜单权限放行后仍受数据权限收口：普通用户默认无规则 → 名录为空。

        内容断言走超管（字段权限白名单制，普通用户无 FieldPermission 时行字段
        全裁剪，见 TestApprovalInstanceApi 同款口径说明）。
        """
        api_client.force_authenticate(user=normal_user)
        grant(role, menu_factory, "list:SystemDirectory", "api/identity/directory$", "GET")
        resp = api_client.get(DIRECTORY_URL)
        assert resp.data["code"] == 1000
        assert resp.data["data"]["total"] == 0


class TestDirectoryScope:
    def test_inactive_users_hidden(self, auth_client, dept, member):
        UserInfo.objects.create_user(username="dir_retired", password="Test@123456", is_active=False, dept=dept)
        resp = auth_client.get(DIRECTORY_URL, {"keyword": "dir_retired"})
        assert resp.data["code"] == 1000
        assert [row["username"] for row in resp.data["data"]["results"]] == []

    def test_keyword_search_covers_nickname(self, auth_client, member):
        resp = auth_client.get(DIRECTORY_URL, {"keyword": "名录成员"})
        assert any(row["username"] == "dir_member" for row in resp.data["data"]["results"])

    def test_dept_filter(self, auth_client, member, dept):
        other_dept = DeptInfo.objects.create(name="财务部", code="dir_fin")
        UserInfo.objects.create_user(username="dir_fin_user", password="Test@123456", dept=other_dept)
        resp = auth_client.get(DIRECTORY_URL, {"dept": str(dept.pk)})
        names = {row["username"] for row in resp.data["data"]["results"]}
        assert "dir_member" in names and "dir_fin_user" not in names

    def test_dept_filter_includes_descendants(self, auth_client, member, dept):
        """部门浏览含下级：点上级部门命中整棵子树成员；子部门不反向包含父部门。"""
        child = DeptInfo.objects.create(name="前端组", code="dir_dev_fe", parent=dept)
        UserInfo.objects.create_user(username="dir_child", password="Test@123456", dept=child)

        resp = auth_client.get(DIRECTORY_URL, {"dept": str(dept.pk)})
        names = {row["username"] for row in resp.data["data"]["results"]}
        assert {"dir_member", "dir_child"}.issubset(names)

        resp = auth_client.get(DIRECTORY_URL, {"dept": str(child.pk)})
        names = {row["username"] for row in resp.data["data"]["results"]}
        assert names == {"dir_child"}

    def test_post_filter_and_labels(self, auth_client, member, dept):
        """岗位筛选仅命中持岗用户；行内岗位标签含 pk/name/code（人员维度展示）。"""
        post = Post.objects.create(name="安全员", code="dir_post", dept=dept)
        member.posts.add(post)
        UserInfo.objects.create_user(username="dir_other", password="Test@123456", dept=dept)

        resp = auth_client.get(DIRECTORY_URL, {"posts": str(post.pk)})
        rows = resp.data["data"]["results"]
        assert {row["username"] for row in rows} == {"dir_member"}
        assert rows[0]["posts"][0]["code"] == "dir_post"

    def test_soft_deleted_post_not_resolved(self, auth_client, member):
        """软删除岗位不是合法筛选值（filterset 默认管理器校验 400，防候选注入）。"""
        from django.utils import timezone

        post = Post.objects.create(name="回收岗位", code="dir_post_recycled")
        member.posts.add(post)
        post.deleted_at = timezone.now()
        post.save(update_fields=["deleted_at"])
        resp = auth_client.get(DIRECTORY_URL, {"posts": str(post.pk)})
        assert resp.status_code == 400


class TestDirectoryTagFilter:
    def _tag_user(self, user, name):
        from django.contrib.contenttypes.models import ContentType

        from system.models import Tag, TaggedItem

        tag = Tag.objects.create(name=name)
        TaggedItem.objects.create(
            content_type=ContentType.objects.get_for_model(type(user)),
            object_id=str(user.pk),
            tag=tag,
        )
        return tag

    @staticmethod
    def _unique(prefix):
        """测试库可能复用：标签名唯一，避免上一次失败残留造成唯一键冲突。"""
        return f"{prefix}-{uuid.uuid4().hex[:8]}"

    def test_tag_filter_hits_tagged_user(self, auth_client, member):
        other = UserInfo.objects.create_user(username="dir_untagged", password="Test@123456")
        tag_name = self._unique("重点")
        self._tag_user(member, tag_name)

        resp = auth_client.get(DIRECTORY_URL, {"tag": tag_name})
        assert resp.data["code"] == 1000
        assert {row["username"] for row in resp.data["data"]["results"]} == {"dir_member"}
        assert other.username not in {row["username"] for row in resp.data["data"]["results"]}

    def test_multi_tag_filter_is_and_semantics(self, auth_client, member):
        first = self._unique("重点")
        second = self._unique("外包")
        self._tag_user(member, first)
        self._tag_user(member, second)

        both = auth_client.get(DIRECTORY_URL, {"tag": f"{first},{second}"})
        assert {row["username"] for row in both.data["data"]["results"]} == {"dir_member"}

        none = auth_client.get(DIRECTORY_URL, {"tag": f"{first},missing-{uuid.uuid4().hex[:8]}"})
        assert none.data["data"]["results"] == []

    def test_tag_choices_exposed_in_search_fields(self, auth_client, member):
        tag_name = self._unique("重点")
        self._tag_user(member, tag_name)
        resp = auth_client.get(f"{DIRECTORY_URL}/search-fields")
        assert resp.data["code"] == 1000
        field = next(item for item in resp.data["data"] if item["key"] == "tag")
        assert {choice["value"] for choice in field["choices"]} >= {tag_name}
