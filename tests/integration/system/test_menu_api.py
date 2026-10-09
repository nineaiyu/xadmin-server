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

    def test_api_url_prefix_whitelist(self, auth_client):
        """返回层前缀白名单：仅业务接口路由（api/ 前缀）进入权限点配置面。"""
        resp = auth_client.get(f"{MENU_URL}/api-url")
        urls = [str(item["url"]) for item in resp.data["data"]]
        assert urls and all(url.startswith("api/") for url in urls)
        # 非业务入口不再返回：文档站 / 任务监控代理 / 「#」哨兵条目
        assert not any(url.startswith(("api-docs", "api/flower")) for url in urls)
        assert "#" not in urls

    def test_api_url_keeps_business_routes(self, auth_client):
        """白名单内路由仍在清单中（权限点 path 选择器与视图下拉的合法所需）。"""
        resp = auth_client.get(f"{MENU_URL}/api-url")
        urls = [str(item["url"]) for item in resp.data["data"]]
        assert "api/identity/user$" in urls
        views = {str(item.get("view") or "") for item in resp.data["data"]}
        assert "identity.views.admin.user.UserViewSet" in views

    def test_api_url_requires_menu_permission(self, api_client, normal_user):
        """端点自身的菜单权限门禁不变：无权限点用户仍被 403 拦截。"""
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(f"{MENU_URL}/api-url")
        assert resp.status_code == 403


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


class TestMenuCacheInvalidation:
    """菜单与菜单元数据的缓存失效：路由快照（TTL 24h）与权限数据必须即时清。

    - MenuMeta 独立保存（改标题/图标/隐藏）不触发 Menu 的 post_save，漏挂最长 24h 不生效；
    - 批量生成权限点曾只失效父菜单，被覆盖更新的子权限点用户最长 24h 持旧权限。
    """

    @staticmethod
    def _prime(user):
        """预热该用户的路由快照 + 权限数据缓存键（只关心是否被清空）。"""
        import time

        from django.core.cache import cache

        keys = [
            f"magic_cache_response_UserRoutesAPIView_get_{user.pk}",
            f"magic_cache_data_get_user_permission_{user.pk}_GET",
        ]
        for key in keys:
            cache.set(key, {"status": "ok", "c_time": time.time(), "data": []}, 300)
        return keys

    def test_menu_meta_change_invalidates_routes(self, menu_factory, normal_user, superuser):
        """菜单元数据独立保存：持有该菜单的角色用户与超管的路由快照都失效。"""
        from django.core.cache import cache

        menu = menu_factory("菜单元数据", menu_type=Menu.MenuChoices.MENU, path="/menu/meta")
        normal_user.roles.first().menu.add(menu)
        keys = self._prime(normal_user) + self._prime(superuser)

        menu.meta.title = "改名后的标题"
        menu.meta.save()

        assert all(cache.get(key) is None for key in keys)

    def test_unbound_menu_meta_change_is_safe(self):
        """未绑定菜单的 meta（先建后绑/级联删除）：保存不报错。"""
        from system.models import MenuMeta

        meta = MenuMeta.objects.create(title="游离元数据")
        meta.title = "游离元数据2"
        meta.save()
        assert MenuMeta.objects.get(pk=meta.pk).title == "游离元数据2"

    def test_bulk_permission_save_invalidates_child_menus(self, auth_client, normal_user):
        """批量生成权限点：被覆盖更新的子权限点用户缓存一并失效（不只父菜单）。"""
        from django.core.cache import cache

        parent = _create_menu(auth_client, name="cache-menu")
        payload = {"views": [TestMenuPermissionPreview.VIEW], "component": "cache-menu"}
        assert auth_client.post(f"{MENU_URL}/{parent}/permissions", payload, format="json").data["code"] == 1000
        child = Menu.objects.filter(name__endswith=":cache-menu").first()
        assert child is not None
        normal_user.roles.first().menu.add(child)
        keys = self._prime(normal_user)

        # 第二次执行全部走「覆盖更新」分支（旧实现只失效父菜单，这些键会残留）
        assert auth_client.post(f"{MENU_URL}/{parent}/permissions", payload, format="json").data["code"] == 1000

        assert all(cache.get(key) is None for key in keys)


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


class TestMenuPermissionAudit:
    """菜单权限检测：只读报告缺口 / 游离权限点 / 重复权限码，不落库。"""

    AUDIT_URL = f"{MENU_URL}/permission-audit"
    ITEM_KEYS = {"problem", "code", "method", "path", "menu", "pk", "view", "suggestion"}

    def test_structure_and_read_only(self, auth_client):
        before = Menu.objects.count()
        resp = auth_client.get(self.AUDIT_URL)
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        data = resp.data["data"]
        assert {"summary", "missing", "orphan", "duplicate", "field_unconfigured"} <= set(data)
        # 库内无权限点时，代码路由全部计入正向缺口
        assert data["summary"]["missing"] == len(data["missing"]) > 0
        summary = data["summary"]
        assert summary["total"] == (
            summary["missing"] + summary["orphan"] + summary["duplicate"] + summary["field_unconfigured"]
        )
        assert set(data["missing"][0]) == self.ITEM_KEYS
        assert data["missing"][0]["problem"] == "missing"
        # 只读：检测不得写入/改动任何菜单
        assert Menu.objects.count() == before

    def test_field_permission_gap_reported(self, auth_client, menu_factory):
        """角色已获模型权限点但未配置字段权限：列入审计（零字段 fail-closed 可见化）。"""
        from identity.models import UserRole
        from system.models import FieldPermission, ModelLabelField

        model_root = ModelLabelField.objects.create(name="identity.post", label="岗位")
        perm = menu_factory("list:SystemPost", path="api/system/post$", method="GET")
        perm.model.add(model_root)
        role = UserRole.objects.create(name="字段权限缺口角色", code="field_gap_role")
        role.menu.add(perm)

        data = auth_client.get(self.AUDIT_URL).data["data"]
        assert data["summary"]["field_unconfigured"] == len(data["field_unconfigured"]) >= 1
        item = next(row for row in data["field_unconfigured"] if row["pk"] == str(perm.pk))
        assert item == {
            "problem": "field_unconfigured",
            "code": "list:SystemPost",
            "method": "GET",
            "path": "api/system/post$",
            "menu": "",
            "role": "字段权限缺口角色",
            "pk": str(perm.pk),
            "view": "",
            "suggestion": "configure",
        }

        # 空字段白名单的 FieldPermission 行同样产出空白名单 → 仍计入缺口
        empty_row = FieldPermission.objects.create(role=role, menu=perm)
        data = auth_client.get(self.AUDIT_URL).data["data"]
        assert any(row["pk"] == str(perm.pk) for row in data["field_unconfigured"])

        # 配好字段白名单后不再报告
        child = ModelLabelField.objects.create(name="name", label="名称", parent=model_root)
        empty_row.field.add(child)
        data = auth_client.get(self.AUDIT_URL).data["data"]
        assert all(row["pk"] != str(perm.pk) for row in data["field_unconfigured"])

    def test_field_permission_gap_ignores_unbound_or_unused(self, auth_client, menu_factory):
        """非模型权限点、未授予角色的权限点不进字段权限审计面。"""
        from identity.models import UserRole
        from system.models import ModelLabelField

        model_root = ModelLabelField.objects.create(name="system.post2", label="岗位2")
        unbound = menu_factory("list:SystemPost2", path="api/system/post2$", method="GET")
        role = UserRole.objects.create(name="其他角色", code="other_role")
        role.menu.add(unbound)  # 未绑定模型 → 不参与字段权限

        data = auth_client.get(self.AUDIT_URL).data["data"]
        assert all(row["pk"] != str(unbound.pk) for row in data["field_unconfigured"])

        granted_elsewhere = menu_factory("list:SystemPost3", path="api/system/post3$", method="GET")
        granted_elsewhere.model.add(model_root)  # 绑定模型但没有任何角色持有
        data = auth_client.get(self.AUDIT_URL).data["data"]
        assert all(row["pk"] != str(granted_elsewhere.pk) for row in data["field_unconfigured"])

    def test_missing_contains_known_route(self, auth_client):
        data = auth_client.get(self.AUDIT_URL).data["data"]
        targets = {(item["method"], item["path"]) for item in data["missing"]}
        assert ("GET", "api/system/menu$") in targets

    def test_orphan_permission_reported(self, auth_client, menu_factory):
        perm = menu_factory("legacyGone:SystemGone", path="api/legacy/gone$", method="GET")
        data = auth_client.get(self.AUDIT_URL).data["data"]
        orphan = [item for item in data["orphan"] if item["pk"] == str(perm.pk)]
        assert len(orphan) == 1
        assert orphan[0]["problem"] == "orphan"
        assert orphan[0]["code"] == "legacyGone:SystemGone"
        assert orphan[0]["method"] == "GET"
        assert orphan[0]["path"] == "api/legacy/gone$"
        assert orphan[0]["suggestion"] == "verify"

    def test_duplicate_permission_reported(self, auth_client, menu_factory):
        menu_factory("dupA:SystemGone", path="api/legacy/dup$", method="GET")
        menu_factory("dupB:SystemGone", path="api/legacy/dup$", method="GET")
        data = auth_client.get(self.AUDIT_URL).data["data"]
        # 同一 (path, method) 的第二条记录计入重复；库序不定，按路径断言
        dup = [item for item in data["duplicate"] if item["path"] == "api/legacy/dup$"]
        assert len(dup) == 1
        assert dup[0]["problem"] == "duplicate"
        assert dup[0]["code"] in {"dupA:SystemGone", "dupB:SystemGone"}
        assert dup[0]["suggestion"] == "merge"

    def test_requires_permission(self, api_client, normal_user):
        """非超管未获授权：检测接口按菜单权限链拒绝（403）。"""
        api_client.force_authenticate(user=normal_user)
        assert api_client.get(self.AUDIT_URL).status_code == 403
