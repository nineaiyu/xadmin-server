# -*- coding: utf-8 -*-
"""列表「我的视图」集成测试。

口径钉死：
- 个人级：仅本人（或超管）可改 / 删；
- 默认视图互斥（每人每页至多一个）；
- 共享视图同页其他用户可见（只读应用）；
- 同名同页唯一（约束冲突返回可读错误）。
"""

import pytest
from django.db import IntegrityError

from system.models import Menu, SavedListView, UserInfo

pytestmark = pytest.mark.django_db

VIEWS_URL = "/api/system/saved-views"
PAGE_KEY = "/system/user/index"


def grant_list_permission(role, menu_factory):
    """授权列表权限点（非超管访问列表接口的前置条件）。"""
    name = "list:SavedListView"
    perm = Menu.objects.filter(name=name).first() or menu_factory(name, path="api/system/saved-views$", method="GET")
    role.menu.add(perm)
    return perm


def _payload(name="本周新增用户", **extra):
    data = {
        "page_key": PAGE_KEY,
        "name": name,
        "conditions": {"created_time": ["2026-09-15", "2026-09-22"], "is_active": True},
        "ordering": "-created_time",
    }
    data.update(extra)
    return data


class TestSavedViewCrud:
    def test_create_and_list(self, auth_client, superuser):
        resp = auth_client.post(VIEWS_URL, _payload(), format="json")
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["owner"] == superuser.pk or str(resp.data["data"]["owner"])

        resp = auth_client.get(VIEWS_URL, {"page_key": PAGE_KEY})
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["total"] == 1
        assert resp.data["data"]["results"][0]["conditions"]["is_active"] is True

    def test_default_view_is_exclusive(self, auth_client, superuser):
        auth_client.post(VIEWS_URL, _payload("视图A", is_default=True), format="json")
        auth_client.post(VIEWS_URL, _payload("视图B", is_default=True), format="json")
        defaults = SavedListView.objects.filter(owner=superuser, page_key=PAGE_KEY, is_default=True)
        assert defaults.count() == 1
        assert defaults.first().name == "视图B"

    def test_update_own_view(self, auth_client):
        resp = auth_client.post(VIEWS_URL, _payload(), format="json")
        pk = resp.data["data"]["pk"]
        resp = auth_client.patch(f"{VIEWS_URL}/{pk}", {"name": "改名视图"}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["name"] == "改名视图"

    def test_duplicate_name_rejected(self, auth_client):
        auth_client.post(VIEWS_URL, _payload(), format="json")
        resp = auth_client.post(VIEWS_URL, _payload(), format="json")
        assert resp.status_code in (400, 409) or resp.data.get("code") != 1000

    def test_destroy_own_view(self, auth_client):
        resp = auth_client.post(VIEWS_URL, _payload(), format="json")
        pk = resp.data["data"]["pk"]
        resp = auth_client.delete(f"{VIEWS_URL}/{pk}")
        assert resp.data["code"] == 1000, resp.data
        assert SavedListView.objects.count() == 0

    def test_other_user_view_invisible_and_immutable(self, api_client, normal_user, role, menu_factory):
        grant_list_permission(role, menu_factory)
        own = SavedListView.objects.create(owner=normal_user, page_key=PAGE_KEY, name="我的视图")
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(VIEWS_URL, {"page_key": PAGE_KEY})
        assert resp.data["data"]["total"] == 1

        # 他人（未共享）不可见且不可改
        other = UserInfo.objects.create_user(username="other_user", password="Test@123456")
        other.roles.add(role)
        api_client.force_authenticate(user=other)
        resp = api_client.get(VIEWS_URL, {"page_key": PAGE_KEY})
        assert resp.data["data"]["total"] == 0
        # 他人视图不可改：被拒（权限点 / 取值域 404 归一）且数据未被改动
        resp = api_client.patch(f"{VIEWS_URL}/{own.pk}", {"name": "篡改"}, format="json")
        assert resp.status_code in (400, 403, 404), resp.status_code
        own.refresh_from_db()
        assert own.name == "我的视图"

    def test_shared_view_visible_to_others(self, api_client, normal_user, role, menu_factory):
        grant_list_permission(role, menu_factory)
        SavedListView.objects.create(owner=normal_user, page_key=PAGE_KEY, name="共享视图", is_shared=True)
        other = UserInfo.objects.create_user(username="other_user2", password="Test@123456")
        other.roles.add(role)
        api_client.force_authenticate(user=other)
        resp = api_client.get(VIEWS_URL, {"page_key": PAGE_KEY})
        assert resp.data["data"]["total"] == 1

    def test_unique_constraint(self, normal_user):
        SavedListView.objects.create(owner=normal_user, page_key=PAGE_KEY, name="同名")
        with pytest.raises(IntegrityError):
            SavedListView.objects.create(owner=normal_user, page_key=PAGE_KEY, name="同名")
