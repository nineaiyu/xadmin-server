# -*- coding: utf-8 -*-
"""system 菜单接口集成测试。"""
import pytest

from system.models import Menu

pytestmark = pytest.mark.django_db

MENU_URL = "/api/system/menu"


def _create_menu(auth_client, name="test-menu", title="测试菜单", **kwargs):
    payload = {"name": name, "path": "/test", "meta": {"title": title}}
    payload.update(kwargs)
    resp = auth_client.post(MENU_URL, payload, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data["code"] == 1000, resp.data
    return resp.data["data"]["pk"]


class TestMenuCrudSmoke:
    def test_create_and_retrieve(self, auth_client):
        pk = _create_menu(auth_client)
        resp = auth_client.get(f"{MENU_URL}/{pk}")
        assert resp.status_code == 200
        assert resp.data["data"]["meta"]["title"] == "测试菜单"

    def test_create_child_menu_with_parent(self, auth_client):
        parent_pk = _create_menu(auth_client, name="parent-menu")
        child_pk = _create_menu(
            auth_client, name="child-menu", title="子菜单", parent=parent_pk, path="/child"
        )
        resp = auth_client.get(f"{MENU_URL}/{child_pk}")
        assert str(resp.data["data"]["parent"]["pk"]) == parent_pk

    def test_patch_toggle_is_active(self, auth_client):
        pk = _create_menu(auth_client)
        resp = auth_client.patch(
            f"{MENU_URL}/{pk}",
            {"is_active": False, "meta": {"title": "测试菜单"}},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["data"]["is_active"] is False

    def test_delete_cascades_meta(self, auth_client):
        pk = _create_menu(auth_client)
        resp = auth_client.delete(f"{MENU_URL}/{pk}")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert not Menu.objects.filter(pk=pk).exists()

    def test_filter_by_name(self, auth_client):
        _create_menu(auth_client, name="system-user", title="用户管理")
        _create_menu(auth_client, name="system-role", title="角色管理")
        resp = auth_client.get(MENU_URL, {"name": "system-user"})
        assert resp.data["data"]["total"] == 1
        assert resp.data["data"]["results"][0]["name"] == "system-user"

    def test_permission_menu_type(self, auth_client):
        pk = _create_menu(
            auth_client,
            name="p-api-list",
            menu_type=Menu.MenuChoices.PERMISSION,
            path="api/demo/book$",
            method="GET",
        )
        resp = auth_client.get(f"{MENU_URL}/{pk}")
        assert resp.data["data"]["menu_type"]["value"] == Menu.MenuChoices.PERMISSION
        assert resp.data["data"]["method"]["value"] == "GET"


class TestMenuApiUrl:
    def test_api_url_action(self, auth_client):
        resp = auth_client.get(f"{MENU_URL}/api-url")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert len(resp.data["data"]) > 0