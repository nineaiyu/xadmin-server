# -*- coding: utf-8 -*-
"""菜单「自动添加API权限」批量生成/覆盖权限点：对外语义守护 + 批量查询回归。"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory

from system.models import Menu, ModelLabelField
from system.views.admin.menu import MenuViewSet

pytestmark = pytest.mark.django_db

PERMISSIONS_URL = "/api/system/menu/{}/permissions"
# 任一已在 URL conf 注册的视图集：按其真实路由批量生成权限点
TARGET_VIEW = "system.views.admin.dict.DataDictViewSet"


def _make_parent(menu_factory):
    return menu_factory(name="perm-gen-parent", menu_type=Menu.MenuChoices.DIRECTORY)


@pytest.fixture
def perm_viewset(superuser):
    """脱离 HTTP 层直接驱动批量构建/落库的视图集实例（带请求用户以补齐审计字段）。"""
    viewset = MenuViewSet()
    request = APIRequestFactory().post("/api/system/menu/")
    request.user = superuser
    viewset.request = request
    return viewset


class TestMenuPermissionGenerateApi:
    """走完整接口链路：新建 / 预览 / 跳过既有 / 覆盖更新的对外语义。"""

    def test_generate_creates_permission_points(self, auth_client, menu_factory, superuser):
        parent = _make_parent(menu_factory)

        resp = auth_client.post(
            PERMISSIONS_URL.format(parent.pk), {"views": [TARGET_VIEW], "component": "PermGen"}, format="json"
        )

        assert resp.status_code == 200
        assert resp.json()["code"] == 1000
        created = Menu.objects.filter(parent=parent, menu_type=Menu.MenuChoices.PERMISSION)
        assert created.exists()
        assert created.filter(name="list:PermGen", method="GET").exists()
        for perm in created:
            assert perm.name.endswith(":PermGen")
            assert perm.is_active is True
            assert perm.meta.title.startswith("C-")
            # creator/modifier 与全局 pre_save 信号补齐的口径一致
            assert perm.creator == superuser
            assert perm.modifier == superuser

    def test_invalid_views_rejected(self, auth_client, menu_factory):
        parent = _make_parent(menu_factory)

        resp = auth_client.post(PERMISSIONS_URL.format(parent.pk), {"views": []}, format="json")

        assert resp.json()["code"] == 1001
        assert not Menu.objects.filter(parent=parent, menu_type=Menu.MenuChoices.PERMISSION).exists()

    def test_dry_run_previews_without_persisting(self, auth_client, menu_factory):
        parent = _make_parent(menu_factory)

        resp = auth_client.post(
            PERMISSIONS_URL.format(parent.pk),
            {"views": [TARGET_VIEW], "component": "PermGen", "dry_run": True},
            format="json",
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["results"]
        assert data["create_count"] == len(data["results"])
        assert data["update_count"] == 0
        assert all(item["action"] == "create" for item in data["results"])
        assert all(item["title"].startswith("C-") for item in data["results"])
        assert not Menu.objects.filter(parent=parent, menu_type=Menu.MenuChoices.PERMISSION).exists()

    def test_skip_existing_keeps_existing_untouched(self, auth_client, menu_factory):
        parent = _make_parent(menu_factory)
        assert (
            auth_client.post(
                PERMISSIONS_URL.format(parent.pk), {"views": [TARGET_VIEW], "component": "PermGen"}, format="json"
            ).status_code
            == 200
        )
        before = {perm.name: (perm.path, perm.meta.title) for perm in Menu.objects.filter(parent=parent)}

        preview = auth_client.post(
            PERMISSIONS_URL.format(parent.pk),
            {"views": [TARGET_VIEW], "component": "PermGen", "skip_existing": True, "dry_run": True},
            format="json",
        )
        assert preview.json()["data"]["results"] == []

        resp = auth_client.post(
            PERMISSIONS_URL.format(parent.pk),
            {"views": [TARGET_VIEW], "component": "PermGen", "skip_existing": True},
            format="json",
        )
        assert resp.status_code == 200
        after = {perm.name: (perm.path, perm.meta.title) for perm in Menu.objects.filter(parent=parent)}
        assert after == before

    def test_regenerate_overwrites_with_update_semantics(self, auth_client, menu_factory, superuser):
        parent = _make_parent(menu_factory)
        assert (
            auth_client.post(
                PERMISSIONS_URL.format(parent.pk), {"views": [TARGET_VIEW], "component": "PermGen"}, format="json"
            ).status_code
            == 200
        )

        preview = auth_client.post(
            PERMISSIONS_URL.format(parent.pk),
            {"views": [TARGET_VIEW], "component": "PermGen", "dry_run": True},
            format="json",
        )
        data = preview.json()["data"]
        assert data["update_count"] == len(data["results"])
        assert data["create_count"] == 0
        assert all(item["action"] == "update" and item["title"].startswith("U-") for item in data["results"])

        resp = auth_client.post(
            PERMISSIONS_URL.format(parent.pk), {"views": [TARGET_VIEW], "component": "PermGen"}, format="json"
        )
        assert resp.status_code == 200
        perms = Menu.objects.filter(parent=parent, menu_type=Menu.MenuChoices.PERMISSION)
        assert perms.count() == len(data["results"])  # 覆盖更新不产生重复行
        assert all(perm.meta.title.startswith("U-") for perm in perms)
        assert all(perm.modifier == superuser for perm in perms)
        assert all(perm.creator == superuser for perm in perms)  # creator 不被覆盖更新改写


class TestMenuPermissionGenerateBatched:
    """批量构建/落库的查询面：存在集与角色模型各一次批量取回，不随权限点数量线性增长。"""

    @staticmethod
    def _permissions(count):
        return [
            {
                "code": f"list:Batch{i}",
                "url": f"/api/system/batch/{i}/",
                "method": "GET",
                "description": f"批量生成权限点 {i}",
                "models": ["system.demo"] if i % 2 else [],
            }
            for i in range(count)
        ]

    def test_build_queries_constant_per_call(self, perm_viewset, menu_factory):
        parent = _make_parent(menu_factory)
        ModelLabelField.objects.create(
            field_type=ModelLabelField.FieldChoices.ROLE, name="system.demo", label="示例模型"
        )

        with CaptureQueriesContext(connection) as ctx:
            items = perm_viewset._build_permission_items(parent, self._permissions(8), skip_existing=False)

        # 存在集 + 角色模型清单各一次批量查询（逐权限点回表时为 16 次）
        assert len(ctx.captured_queries) == 2
        assert [action for action, _menu, _data in items] == ["create"] * 8
        assert all(item[2]["meta"]["title"].startswith("C-") for item in items)
        assert [item[2]["rank"] for item in items] == list(range(10001, 10009))

    def test_build_reuses_existing_menu_with_update_semantics(self, perm_viewset, menu_factory):
        parent = _make_parent(menu_factory)
        existing = menu_factory(name="list:Batch0", method="GET")

        with CaptureQueriesContext(connection) as ctx:
            items = perm_viewset._build_permission_items(parent, self._permissions(2), skip_existing=False)

        assert len(ctx.captured_queries) == 2
        by_name = {data["name"]: (action, menu) for action, menu, data in items}
        assert by_name["list:Batch0"][0] == "update"
        assert by_name["list:Batch0"][1].pk == existing.pk
        assert by_name["list:Batch1"][0] == "create"
        titles = {data["name"]: data["meta"]["title"] for _action, _menu, data in items}
        assert titles["list:Batch0"].startswith("U-")
        assert titles["list:Batch1"].startswith("C-")

    def test_build_skips_existing_when_asked(self, perm_viewset, menu_factory):
        parent = _make_parent(menu_factory)
        menu_factory(name="list:Batch0", method="GET")

        items = perm_viewset._build_permission_items(parent, self._permissions(2), skip_existing=True)

        assert [data["name"] for _action, _menu, data in items] == ["list:Batch1"]

    def test_save_syncs_role_model_links(self, perm_viewset, menu_factory, superuser):
        parent = _make_parent(menu_factory)
        ModelLabelField.objects.create(
            field_type=ModelLabelField.FieldChoices.ROLE, name="system.demo", label="示例模型"
        )
        ModelLabelField.objects.create(
            field_type=ModelLabelField.FieldChoices.ROLE, name="system.gone", label="移除模型"
        )

        def _build(models):
            return perm_viewset._build_permission_items(
                parent,
                [
                    {
                        "code": "list:Sync",
                        "url": "/api/system/sync/",
                        "method": "GET",
                        "description": "同步",
                        "models": models,
                    }
                ],
                skip_existing=False,
            )

        saved = perm_viewset._save_permission_items(_build(["system.demo", "system.gone"]))
        assert len(saved) == 1
        assert set(saved[0].model.values_list("name", flat=True)) == {"system.demo", "system.gone"}
        assert saved[0].creator == superuser
        assert saved[0].meta.title.startswith("C-")

        # 覆盖更新：角色模型关联按差集收敛到目标清单
        saved = perm_viewset._save_permission_items(_build(["system.demo"]))
        assert set(saved[0].model.values_list("name", flat=True)) == {"system.demo"}
        assert saved[0].meta.title.startswith("U-")
        assert saved[0].modifier == superuser

        # 目标清单清空：关联整体移除
        saved = perm_viewset._save_permission_items(_build([]))
        assert list(saved[0].model.all()) == []

    def test_save_query_count_bounded(self, perm_viewset, menu_factory):
        parent = _make_parent(menu_factory)
        items = perm_viewset._build_permission_items(parent, self._permissions(10), skip_existing=False)

        with CaptureQueriesContext(connection) as ctx:
            saved = perm_viewset._save_permission_items(items)

        assert len(saved) == 10
        # 批量写入仅需个位数语句；逐条校验+保存时为每权限点多条（约 5×10）
        assert len(ctx.captured_queries) <= 12
