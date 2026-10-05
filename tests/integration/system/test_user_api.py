# -*- coding: utf-8 -*-
"""system 用户接口集成测试。"""

import pytest

from common.base.utils import AESCipherV2
from identity.models import Post, UserInfo
from tests.integration.aes_v2 import encrypt_v2

pytestmark = pytest.mark.django_db

USER_URL = "/api/system/user"
CONFIRM_URL = "/api/mfa/confirm"


@pytest.fixture
def confirmed_client(auth_client):
    """已通过密码二次确认的管理员客户端（删除用户为敏感操作，需先验证）。"""
    resp = auth_client.post(
        CONFIRM_URL,
        {"confirm_type": "password", "method": "password", "code": "Admin@123456"},
        format="json",
    )
    assert resp.data["code"] == 1000, resp.data
    return auth_client


def _create_user(auth_client, username="lisi", **kwargs):
    payload = {"username": username, "nickname": "李四", "password": "Test@123456"}
    payload.update(kwargs)
    resp = auth_client.post(USER_URL, payload, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data["code"] == 1000, resp.data
    return resp.data["data"]["pk"]


class TestUserCrudSmoke:
    def test_create_list_retrieve_patch_delete(self, confirmed_client, role):
        auth_client = confirmed_client
        pk = _create_user(auth_client, roles=[role.pk])

        resp = auth_client.get(USER_URL, {"username": "lisi"})
        assert resp.status_code == 200
        assert resp.data["data"]["total"] == 1
        assert resp.data["data"]["results"][0]["username"] == "lisi"

        resp = auth_client.get(f"{USER_URL}/{pk}")
        assert resp.status_code == 200
        assert resp.data["data"]["pk"] == pk

        resp = auth_client.patch(f"{USER_URL}/{pk}", {"nickname": "改名了"}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["data"]["nickname"] == "改名了"

        resp = auth_client.delete(f"{USER_URL}/{pk}")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert not UserInfo.objects.filter(pk=pk).exists()

    def test_create_duplicate_username(self, auth_client):
        _create_user(auth_client, username="lisi")
        resp = auth_client.post(USER_URL, {"username": "lisi", "password": "Test@123456"}, format="json")
        assert resp.data["code"] != 1000

    def test_search_filter_by_nickname(self, auth_client):
        _create_user(auth_client, username="lisi", nickname="李四")
        _create_user(auth_client, username="wangwu", nickname="王五")
        resp = auth_client.get(USER_URL, {"nickname": "李"})
        assert resp.data["data"]["total"] == 1
        assert resp.data["data"]["results"][0]["username"] == "lisi"


class TestUserPostsWritable:
    """用户表单内直接编辑岗位（与角色同口径；岗位页成员分配仍互为补充）。"""

    def test_create_with_posts_and_patch(self, auth_client):
        post_a = Post.objects.create(name="安全员", code="safety")
        post_b = Post.objects.create(name="质检员", code="qc")

        pk = _create_user(auth_client, posts=[post_a.pk])
        user = UserInfo.objects.get(pk=pk)
        assert list(user.posts.values_list("code", flat=True)) == ["safety"]

        resp = auth_client.patch(f"{USER_URL}/{pk}", {"posts": [post_b.pk]}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        user.refresh_from_db()
        assert list(user.posts.values_list("code", flat=True)) == ["qc"]

    def test_patch_posts_empty_clears(self, auth_client):
        post = Post.objects.create(name="安全员", code="safety")
        pk = _create_user(auth_client, posts=[post.pk])
        resp = auth_client.patch(f"{USER_URL}/{pk}", {"posts": []}, format="json")
        assert resp.data["code"] == 1000, resp.data
        user = UserInfo.objects.get(pk=pk)
        assert user.posts.count() == 0


class TestUserActionsSmoke:
    def test_delete_superuser_forbidden(self, confirmed_client):
        """超管禁止删除：可读业务错误（400 + 文案），不是 500。

        历史行为是 perform_destroy 抛裸 Exception → 归一 500，前端只能看到
        「服务器错误」，排查与提示都失真；这里锁定 400 + 可读 detail。
        """
        auth_client = confirmed_client
        pk = UserInfo.objects.create_superuser(username="admin2", email="a2@example.com", password="Admin@123456").pk
        resp = auth_client.delete(f"{USER_URL}/{pk}")
        assert resp.status_code == 400
        assert resp.data["code"] == 400  # ValidationError 的业务码口径 = HTTP 状态码
        assert "超级管理员" in str(resp.data["detail"])
        assert UserInfo.objects.filter(pk=pk).exists()

    def test_batch_destroy_excludes_superuser(self, confirmed_client):
        auth_client = confirmed_client
        normal_pk = _create_user(auth_client, username="lisi")
        super_pk = UserInfo.objects.create_superuser(
            username="admin2", email="a2@example.com", password="Admin@123456"
        ).pk
        resp = auth_client.post(f"{USER_URL}/batch-destroy", [normal_pk, super_pk], format="json")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert not UserInfo.objects.filter(pk=normal_pk).exists()
        assert UserInfo.objects.filter(pk=super_pk).exists()

    def test_reset_password(self, auth_client):
        pk = _create_user(auth_client, username="lisi")
        encrypted = AESCipherV2("lisi").encrypt(b"NewPass@123456").decode()
        resp = auth_client.post(f"{USER_URL}/{pk}/reset-password", {"password": encrypted}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        assert UserInfo.objects.get(pk=pk).check_password("NewPass@123456")

    def test_reset_password_with_v2_encrypted_payload(self, auth_client):
        """v2 协议（WebCrypto PBKDF2+AES-GCM）密文走真实 API 解密链路。"""
        pk = _create_user(auth_client, username="lisi")
        encrypted = encrypt_v2("lisi", "NewPass@123456")
        resp = auth_client.post(f"{USER_URL}/{pk}/reset-password", {"password": encrypted}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        assert UserInfo.objects.get(pk=pk).check_password("NewPass@123456")

    def test_create_user_with_v2_encrypted_password(self, auth_client):
        """新增用户密码为 v2 协议密文（前端 beforeSubmit 异步加密后提交）。"""
        resp = auth_client.post(
            USER_URL,
            {"username": "v2user", "nickname": "V2", "password": encrypt_v2("v2user", "Test@123456")},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        assert UserInfo.objects.get(username="v2user").check_password("Test@123456")

    def test_unblock(self, auth_client):
        pk = _create_user(auth_client, username="lisi")
        resp = auth_client.post(f"{USER_URL}/{pk}/unblock", {}, format="json")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000


class TestUserPosts:
    """用户列表岗位只读回显与按岗位筛选（岗位写入收口在岗位页）。"""

    def test_list_returns_post_brief(self, auth_client):
        post = Post.objects.create(name="安全员", code="safety", rank=10)
        user = UserInfo.objects.create_user(username="wangwu", password="Test@123456", nickname="王五")
        user.posts.add(post)

        resp = auth_client.get(USER_URL, {"username": "wangwu"})
        assert resp.status_code == 200, resp.data
        row = resp.data["data"]["results"][0]
        assert row["posts"] == [{"pk": post.pk, "name": "安全员", "code": "safety", "label": "安全员"}]

    def test_user_without_post_returns_empty_list(self, auth_client):
        UserInfo.objects.create_user(username="nopost", password="Test@123456")
        resp = auth_client.get(USER_URL, {"username": "nopost"})
        assert resp.data["data"]["results"][0]["posts"] == []

    def test_filter_by_single_post(self, auth_client):
        post = Post.objects.create(name="安全员", code="safety")
        other = Post.objects.create(name="审计员", code="audit")
        in_post = UserInfo.objects.create_user(username="inpost", password="Test@123456")
        in_post.posts.add(post)
        other_user = UserInfo.objects.create_user(username="other", password="Test@123456")
        other_user.posts.add(other)
        UserInfo.objects.create_user(username="nopost", password="Test@123456")

        resp = auth_client.get(USER_URL, {"posts": str(post.pk)})
        assert resp.status_code == 200, resp.data
        assert {row["username"] for row in resp.data["data"]["results"]} == {"inpost"}

    def test_filter_by_multiple_posts_matches_any(self, auth_client):
        post = Post.objects.create(name="安全员", code="safety")
        other = Post.objects.create(name="审计员", code="audit")
        p1 = UserInfo.objects.create_user(username="p1", password="Test@123456")
        p1.posts.add(post)
        p2 = UserInfo.objects.create_user(username="p2", password="Test@123456")
        p2.posts.add(other)
        UserInfo.objects.create_user(username="nopost", password="Test@123456")

        resp = auth_client.get(USER_URL, {"posts": [str(post.pk), str(other.pk)]})
        assert resp.status_code == 200, resp.data
        assert {row["username"] for row in resp.data["data"]["results"]} == {"p1", "p2"}

    def test_no_post_filter_keeps_users_without_post(self, auth_client):
        UserInfo.objects.create_user(username="nopost", password="Test@123456")
        resp = auth_client.get(USER_URL, {"username": "nopost"})
        assert resp.data["data"]["total"] == 1

    def test_search_fields_expose_posts_select(self, auth_client):
        post = Post.objects.create(name="安全员", code="safety")
        resp = auth_client.get(f"{USER_URL}/search-fields")
        assert resp.status_code == 200, resp.data
        fields = {item["key"]: item for item in resp.data["data"]}
        assert fields["posts"]["input_type"] == "select-multiple"
        # 下拉候选由岗位列表数据生成（复用既有元数据，无新增端点）
        assert [choice["value"] for choice in fields["posts"]["choices"]] == [str(post.pk)]

    def test_search_columns_include_writable_posts(self, auth_client):
        """岗位在用户侧可写（与角色同口径）：用户表单内可直接编辑岗位。"""
        resp = auth_client.get(f"{USER_URL}/search-columns")
        assert resp.status_code == 200, resp.data
        columns = {item["key"]: item for item in resp.data["data"]}
        assert columns["posts"]["read_only"] is False
        assert columns["posts"]["multiple"] is True
        assert "table_show" in columns["posts"]
