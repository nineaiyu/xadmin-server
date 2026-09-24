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
        child_pk = _create_menu(auth_client, name="child-menu", title="子菜单", parent=parent_pk, path="/child")
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

    def test_patch_is_active_without_meta(self, auth_client):
        """行内启停只提交 is_active：PATCH 不携带 meta 时跳过 meta 更新，不得 500。

        菜单的行内/批量启停是最高频操作，历史实现强制 pop("meta") 会让这类
        字段级局部更新直接 KeyError（同时打穿批量更新链路）。
        """
        pk = _create_menu(auth_client)
        resp = auth_client.patch(f"{MENU_URL}/{pk}", {"is_active": False}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["data"]["is_active"] is False
        instance = Menu.objects.get(pk=pk)
        assert instance.is_active is False
        assert instance.meta.title == "测试菜单"

    def test_delete_cascades_meta(self, auth_client):
        pk = _create_menu(auth_client)
        resp = auth_client.delete(f"{MENU_URL}/{pk}")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert not Menu.objects.filter(pk=pk).exists()

    def test_patch_meta_watermark(self, auth_client):
        """菜单级水印开关：meta 部分更新可持久化（新菜单默认关闭）。"""
        pk = _create_menu(auth_client)
        assert auth_client.get(f"{MENU_URL}/{pk}").data["data"]["meta"]["watermark"] is False

        resp = auth_client.patch(f"{MENU_URL}/{pk}", {"meta": {"watermark": True}}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["data"]["meta"]["watermark"] is True
        assert Menu.objects.get(pk=pk).meta.watermark is True

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


class TestMenuBatchUpdate:
    """批量启停：白名单只放开 is_active，逐项走序列化器校验。"""

    def test_batch_disable(self, auth_client):
        first = _create_menu(auth_client, name="batch-menu-a")
        second = _create_menu(auth_client, name="batch-menu-b")
        resp = auth_client.post(
            f"{MENU_URL}/batch-update",
            {"pks": [first, second], "fields": {"is_active": False}},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["updated"] == 2
        assert Menu.objects.filter(pk__in=[first, second], is_active=False).count() == 2

    def test_batch_update_rejects_other_fields(self, auth_client):
        pk = _create_menu(auth_client, name="batch-menu-c")
        resp = auth_client.post(
            f"{MENU_URL}/batch-update",
            {"pks": [pk], "fields": {"path": "/hacked"}},
            format="json",
        )
        assert resp.data["code"] == 1004
        assert Menu.objects.get(pk=pk).path == "/test"


class TestMenuPermissionPreview:
    """权限码批量生成：dry_run 预览与执行共用同一构造逻辑（不落库）。"""

    VIEW = "system.views.admin.menu.MenuViewSet"

    def test_dry_run_does_not_persist(self, auth_client):
        pk = _create_menu(auth_client, name="preview-menu-a")
        before = Menu.objects.filter(name__endswith=":preview-menu-a").count()
        resp = auth_client.post(
            f"{MENU_URL}/{pk}/permissions",
            {"views": [self.VIEW], "component": "preview-menu-a", "dry_run": True},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        data = resp.data["data"]
        assert data["create_count"] > 0
        assert data["update_count"] == 0
        assert all(item["action"] == "create" for item in data["results"])
        assert all(item["name"].endswith(":preview-menu-a") for item in data["results"])
        assert Menu.objects.filter(name__endswith=":preview-menu-a").count() == before

    def test_dry_run_marks_existing_as_update(self, auth_client):
        pk = _create_menu(auth_client, name="preview-menu-b")
        payload = {"views": [self.VIEW], "component": "preview-menu-b"}
        preview = auth_client.post(f"{MENU_URL}/{pk}/permissions", {**payload, "dry_run": True}, format="json")
        expected = preview.data["data"]["create_count"]
        assert expected > 0

        created = auth_client.post(f"{MENU_URL}/{pk}/permissions", payload, format="json")
        assert created.data["code"] == 1000, created.data

        again = auth_client.post(f"{MENU_URL}/{pk}/permissions", {**payload, "dry_run": True}, format="json")
        assert again.data["data"]["update_count"] == expected
        assert again.data["data"]["create_count"] == 0
        assert all(item["action"] == "update" for item in again.data["data"]["results"])
