# -*- coding: utf-8 -*-
"""system 用户接口集成测试。"""
import pytest

from common.base.utils import AESCipherV2
from system.models import UserInfo

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
        resp = auth_client.post(
            USER_URL, {"username": "lisi", "password": "Test@123456"}, format="json"
        )
        assert resp.data["code"] != 1000

    def test_search_filter_by_nickname(self, auth_client):
        _create_user(auth_client, username="lisi", nickname="李四")
        _create_user(auth_client, username="wangwu", nickname="王五")
        resp = auth_client.get(USER_URL, {"nickname": "李"})
        assert resp.data["data"]["total"] == 1
        assert resp.data["data"]["results"][0]["username"] == "lisi"


class TestUserActionsSmoke:
    def test_delete_superuser_forbidden(self, confirmed_client):
        auth_client = confirmed_client
        pk = UserInfo.objects.create_superuser(
            username="admin2", email="a2@example.com", password="Admin@123456"
        ).pk
        resp = auth_client.delete(f"{USER_URL}/{pk}")
        assert resp.status_code == 500
        assert UserInfo.objects.filter(pk=pk).exists()

    def test_batch_destroy_excludes_superuser(self, confirmed_client):
        auth_client = confirmed_client
        normal_pk = _create_user(auth_client, username="lisi")
        super_pk = UserInfo.objects.create_superuser(
            username="admin2", email="a2@example.com", password="Admin@123456"
        ).pk
        resp = auth_client.post(
            f"{USER_URL}/batch-destroy", [normal_pk, super_pk], format="json"
        )
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert not UserInfo.objects.filter(pk=normal_pk).exists()
        assert UserInfo.objects.filter(pk=super_pk).exists()

    def test_reset_password(self, auth_client):
        pk = _create_user(auth_client, username="lisi")
        encrypted = AESCipherV2("lisi").encrypt(b"NewPass@123456").decode()
        resp = auth_client.post(
            f"{USER_URL}/{pk}/reset-password", {"password": encrypted}, format="json"
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        assert UserInfo.objects.get(pk=pk).check_password("NewPass@123456")

    def test_unblock(self, auth_client):
        pk = _create_user(auth_client, username="lisi")
        resp = auth_client.post(f"{USER_URL}/{pk}/unblock", {}, format="json")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000