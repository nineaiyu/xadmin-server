# -*- coding: utf-8 -*-
"""扩展：菜单软删除与回收站（目录级联标记后代、成组恢复/清除、名称释放）。"""
import pytest

from system.models import Menu, MenuMeta

pytestmark = pytest.mark.django_db

MENU_URL = "/api/system/menu"


class TestMenuRecycleBin:
    def _tree(self, menu_factory):
        directory = menu_factory(name="系统目录", menu_type=Menu.MenuChoices.DIRECTORY)
        page = menu_factory(name="用户页面", menu_type=Menu.MenuChoices.MENU, parent=directory,
                            path="api/system/user$")
        button = menu_factory(name="list:User", parent=page, path=r"api/system/user$", method="GET")
        return directory, page, button

    def test_soft_delete_directory_cascades_descendants(self, auth_client, menu_factory):
        directory, page, button = self._tree(menu_factory)
        resp = auth_client.delete(f"{MENU_URL}/{directory.pk}")
        assert resp.data["code"] == 1000, resp.data
        for menu in (directory, page, button):
            menu.refresh_from_db()
            assert menu.deleted_at is not None
        # 后代与目录同一时间戳（成组恢复/清除的口径）
        assert page.deleted_at == directory.deleted_at
        assert button.deleted_at == directory.deleted_at

    def test_delete_leaf_keeps_ancestors(self, auth_client, menu_factory):
        directory, page, button = self._tree(menu_factory)
        auth_client.delete(f"{MENU_URL}/{button.pk}")
        directory.refresh_from_db()
        page.refresh_from_db()
        assert directory.deleted_at is None and page.deleted_at is None

    def test_restore_restores_group(self, auth_client, menu_factory):
        directory, page, button = self._tree(menu_factory)
        auth_client.delete(f"{MENU_URL}/{directory.pk}")
        resp = auth_client.patch(f"{MENU_URL}/recycle/restore", {"pks": [str(directory.pk)]}, format="json")
        assert resp.data["code"] == 1000, resp.data
        for menu in (directory, page, button):
            menu.refresh_from_db()
            assert menu.deleted_at is None

    def test_purge_removes_menu_and_meta(self, auth_client, menu_factory):
        directory, page, button = self._tree(menu_factory)
        meta_id = button.meta_id
        auth_client.delete(f"{MENU_URL}/{button.pk}")
        resp = auth_client.delete(f"{MENU_URL}/recycle/purge", {"pks": [str(button.pk)]}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert not Menu.all_objects.filter(pk=button.pk).exists()
        assert not MenuMeta.objects.filter(pk=meta_id).exists()

    def test_purge_directory_removes_group(self, auth_client, menu_factory):
        """成组清除：物理清除目录时，同时间戳的后代一并物理清除。"""
        directory, page, button = self._tree(menu_factory)
        auth_client.delete(f"{MENU_URL}/{directory.pk}")
        resp = auth_client.delete(f"{MENU_URL}/recycle/purge", {"pks": [str(directory.pk)]}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert not Menu.all_objects.filter(pk__in=[directory.pk, page.pk, button.pk]).exists()

    def test_deleted_menu_releases_name(self, auth_client, menu_factory):
        """已删除菜单释放 name，可创建同名新菜单。"""
        directory, page, button = self._tree(menu_factory)
        auth_client.delete(f"{MENU_URL}/{button.pk}")
        resp = auth_client.post(
            MENU_URL,
            {"name": button.name, "path": r"api/system/user$", "method": "GET",
             "menu_type": Menu.MenuChoices.PERMISSION, "meta": {"title": "同名新菜单"}},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data

    def test_duplicate_active_name_rejected(self, auth_client, menu_factory):
        directory, page, button = self._tree(menu_factory)
        resp = auth_client.post(
            MENU_URL,
            {"name": button.name, "path": r"api/system/user$", "method": "GET",
             "menu_type": Menu.MenuChoices.PERMISSION, "meta": {"title": "重复名"}},
            format="json",
        )
        assert resp.data["code"] != 1000

    def test_batch_destroy_cascades_descendants(self, auth_client, menu_factory):
        directory, page, button = self._tree(menu_factory)
        resp = auth_client.post(f"{MENU_URL}/batch-destroy", [str(directory.pk)], format="json")
        assert resp.data["code"] == 1000, resp.data
        for menu in (directory, page, button):
            menu.refresh_from_db()
            assert menu.deleted_at is not None
