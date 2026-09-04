# -*- coding: utf-8 -*-
"""common/core/filter.py 过滤单元测试（以 system.UserViewSet 列表为载体）。

经 HTTP 走 DjangoFilterBackend + BaseDataPermissionFilter 组合，验证
?field=keyword 单字段过滤与多字段组合过滤。注意：superuser 跳过数据权限过滤，
因此结果稳定。
"""
import pytest

from system.models import UserInfo

pytestmark = pytest.mark.django_db

USER_LIST_URL = "/api/system/user"


@pytest.fixture
def users(db):
    """创建互不冲突的用户（避免与 conftest 的 admin/zhangsan 重名）。"""
    UserInfo.objects.create_user(username="zhang_san", password="Test@123456", nickname="阿张")
    UserInfo.objects.create_user(username="zhang_wei", password="Test@123456", nickname="小张")
    UserInfo.objects.create_user(username="li_si", password="Test@123456", nickname="阿李")
    return None


class TestUserViewSetFilter:
    def test_filter_single_username_field(self, auth_client, users):
        resp = auth_client.get(USER_LIST_URL, {"username": "zhang"})
        assert resp.status_code == 200
        assert resp.data["data"]["total"] == 2
        names = {u["username"] for u in resp.data["data"]["results"]}
        assert names == {"zhang_san", "zhang_wei"}

    def test_filter_single_nickname_field(self, auth_client, users):
        resp = auth_client.get(USER_LIST_URL, {"nickname": "阿"})
        assert resp.status_code == 200
        names = {u["username"] for u in resp.data["data"]["results"]}
        assert names == {"zhang_san", "li_si"}

    def test_filter_multiple_fields_combined(self, auth_client, users):
        """username 与 nickname 组合过滤（交集）。"""
        resp = auth_client.get(USER_LIST_URL, {"username": "zhang", "nickname": "阿"})
        assert resp.status_code == 200
        names = {u["username"] for u in resp.data["data"]["results"]}
        assert names == {"zhang_san"}

    def test_filter_no_match_returns_empty(self, auth_client, users):
        resp = auth_client.get(USER_LIST_URL, {"username": "not_exist"})
        assert resp.status_code == 200
        assert resp.data["data"]["total"] == 0

    def test_filter_is_active_field(self, auth_client, users):
        """布尔字段过滤（默认 is_active=True 用户均可见）。"""
        resp = auth_client.get(USER_LIST_URL, {"is_active": "true"})
        assert resp.status_code == 200
        assert {u["username"] for u in resp.data["data"]["results"]} >= {"zhang_san", "zhang_wei", "li_si"}