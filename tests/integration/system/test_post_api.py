# -*- coding: utf-8 -*-
"""岗位（Post）集成测试。

口径钉死：
1. CRUD 与批量启停用（软删除进回收站，名称/编码在未删除数据内唯一）；
2. 成员分配为增量 `{add, remove}`（幂等），成员数与列表注解一致；
3. 成员查看（GET）与分配（POST）是两个独立端点（便于只读名录授权）；
4. 选人候选（user-options）只回 pk/用户名/昵称且受 list 权限同口径。
"""

import json

import pytest

from identity.models import Post

pytestmark = pytest.mark.django_db

POST_URL = "/api/system/posts"


def _create(client, name="安全员", code="safety", **extra):
    payload = {"name": name, "code": code, "rank": 10, **extra}
    return client.post(POST_URL, payload)


def _results(resp):
    return resp.json()["data"]["results"]


class TestPostCrud:
    def test_create_and_list(self, auth_client):
        resp = _create(auth_client, description="全组织通用岗")
        assert resp.json()["code"] == 1000, resp.json()
        pk = resp.json()["data"]["pk"]

        body = auth_client.get(POST_URL, {"name": "安全"}).json()["data"]
        assert body["total"] == 1
        row = body["results"][0]
        assert row["pk"] == pk
        assert row["name"] == "安全员"
        assert row["user_count"] == 0
        assert row["dept_name"] == ""

    def test_name_and_code_must_be_unique(self, auth_client):
        assert _create(auth_client).json()["code"] == 1000
        # DRF 字段级唯一校验（单字段 UniqueConstraint 自动生成校验器）：
        # 返回 400 且响应体内定位到冲突字段，不是数据库 IntegrityError
        dup_name = _create(auth_client, code="other")
        assert dup_name.status_code == 400, dup_name.json()
        assert "name" in json.dumps(dup_name.json(), ensure_ascii=False)
        dup_code = _create(auth_client, name="另一个", code="safety")
        assert dup_code.status_code == 400, dup_code.json()
        assert "code" in json.dumps(dup_code.json(), ensure_ascii=False)

    def test_soft_deleted_name_reusable(self, auth_client):
        pk = _create(auth_client).json()["data"]["pk"]
        assert auth_client.delete(f"{POST_URL}/{pk}").json()["code"] == 1000
        # 软删除释放名称与编码（唯一约束只作用于未删除数据）
        again = _create(auth_client)
        assert again.json()["code"] == 1000, again.json()

    def test_partial_update_and_delete_moves_to_recycle(self, auth_client):
        pk = _create(auth_client).json()["data"]["pk"]
        resp = auth_client.patch(f"{POST_URL}/{pk}", {"rank": 5, "is_active": False}, format="json")
        assert resp.json()["code"] == 1000
        assert Post.objects.get(pk=pk).rank == 5
        assert Post.objects.get(pk=pk).is_active is False

        resp = auth_client.delete(f"{POST_URL}/{pk}")
        assert resp.json()["code"] == 1000
        assert Post.objects.filter(pk=pk).count() == 0, "软删除后默认管理器不可见"
        recycle = auth_client.get(f"{POST_URL}/recycle").json()["data"]
        assert recycle["total"] == 1

    def test_batch_update_is_active(self, auth_client):
        pk1 = _create(auth_client, name="岗A", code="a").json()["data"]["pk"]
        pk2 = _create(auth_client, name="岗B", code="b").json()["data"]["pk"]
        resp = auth_client.post(
            f"{POST_URL}/batch-update",
            {"pks": [pk1, pk2], "fields": {"is_active": False}, "_write_marker": "batchUpdate"},
            format="json",
        )
        assert resp.json()["code"] == 1000
        assert Post.objects.filter(is_active=False).count() == 2


class TestPostMembers:
    def test_assign_add_and_remove(self, auth_client, normal_user, superuser):
        from identity.models import UserInfo

        pk = _create(auth_client).json()["data"]["pk"]
        member = UserInfo.objects.create_user(username="member", password="Test@123456", nickname="成员")

        resp = auth_client.post(f"{POST_URL}/{pk}/assign", {"add": [member.pk]}, format="json")
        assert resp.json()["code"] == 1000, resp.json()
        assert [item["pk"] for item in resp.json()["data"]["members"]] == [member.pk]
        assert Post.objects.get(pk=pk).users.count() == 1

        # 幂等：重复添加不产生第二条关联
        auth_client.post(f"{POST_URL}/{pk}/assign", {"add": [member.pk]}, format="json")
        assert Post.objects.get(pk=pk).users.count() == 1

        # 列表计数与成员一致
        row = _results(auth_client.get(POST_URL, {"name": "安全"}))[0]
        assert row["user_count"] == 1

        resp = auth_client.post(f"{POST_URL}/{pk}/assign", {"remove": [member.pk]}, format="json")
        assert resp.json()["code"] == 1000
        assert resp.json()["data"]["members"] == []
        assert Post.objects.get(pk=pk).users.count() == 0

    def test_members_view_is_read_only_endpoint(self, auth_client):
        pk = _create(auth_client).json()["data"]["pk"]
        resp = auth_client.get(f"{POST_URL}/{pk}/members")
        assert resp.json()["code"] == 1000
        assert resp.json()["data"]["members"] == []
        # 分配走独立端点：对 members 直接 POST 应 405（路径不存在 POST）
        assert auth_client.post(f"{POST_URL}/{pk}/members", {}, format="json").status_code == 405

    def test_assign_requires_change_payload(self, auth_client):
        pk = _create(auth_client).json()["data"]["pk"]
        resp = auth_client.post(f"{POST_URL}/{pk}/assign", {}, format="json")
        assert resp.status_code == 400, resp.json()

    def test_assign_invalid_member_id_returns_readable_error(self, auth_client):
        pk = _create(auth_client).json()["data"]["pk"]
        resp = auth_client.post(f"{POST_URL}/{pk}/assign", {"add": ["not-a-pk"]}, format="json")
        assert resp.json()["code"] == 1001, resp.json()

    def test_inactive_user_not_added(self, auth_client):
        from identity.models import UserInfo

        pk = _create(auth_client).json()["data"]["pk"]
        inactive = UserInfo.objects.create_user(username="inactive", password="Test@123456")
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])
        resp = auth_client.post(f"{POST_URL}/{pk}/assign", {"add": [inactive.pk]}, format="json")
        assert resp.json()["code"] == 1000
        assert Post.objects.get(pk=pk).users.count() == 0, "失效用户不进入成员"

    def test_user_options_returns_brief(self, auth_client):
        from identity.models import UserInfo

        UserInfo.objects.create_user(username="searchme", password="Test@123456", nickname="可搜索")
        resp = auth_client.get(f"{POST_URL}/user-options", {"keyword": "searchme"})
        assert resp.json()["code"] == 1000
        rows = resp.json()["data"]
        assert rows and set(rows[0]) == {"pk", "username", "nickname"}


class TestSearchPostCandidates:
    """岗位搜索候选（/api/system/search/post）：选人下拉与通讯录岗位视角共用。

    候选清单带 user_count（人员名录展示岗位规模），且只含启用未删除岗位。
    """

    SEARCH_URL = "/api/system/search/post"

    def test_candidates_carry_user_count(self, auth_client):
        from identity.models import UserInfo

        post = Post.objects.create(name="候选岗", code="cand_post")
        member = UserInfo.objects.create_user(username="cand_user", password="Test@123456")
        member.posts.add(post)

        resp = auth_client.get(self.SEARCH_URL, {"name": "候选岗"})
        assert resp.json()["code"] == 1000, resp.json()
        rows = resp.json()["data"]["results"]
        assert rows and rows[0]["user_count"] == 1

    def test_candidates_exclude_inactive(self, auth_client):
        Post.objects.create(name="停用岗", code="cand_off", is_active=False)
        resp = auth_client.get(self.SEARCH_URL, {"name": "停用岗"})
        assert resp.json()["code"] == 1000
        assert resp.json()["data"]["results"] == []
