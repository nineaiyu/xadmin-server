# -*- coding: utf-8 -*-
"""system/services/permission_sync/：菜单权限点同步内核（纯逻辑 + 轻量 DB）。"""

import json
from types import SimpleNamespace

import pytest

from system.services import permission_sync as sync


class TestUrlSampling:
    def test_url_to_sample_replaces_regex_groups(self):
        assert sync.url_to_sample("api/identity/user/(?P<pk>[^/.]+)$") == "/api/identity/user/1"
        assert sync.url_to_sample("api/system/monitor/task-health$") == "/api/system/monitor/task-health"
        assert sync.url_to_sample("api/identity/user$") == "/api/identity/user"

    def test_path_whitelisted(self):
        # 登出与个人凭证端点在白名单内（任意方法），异步导出不在
        # （登录端点自带匿名权限类，不登记白名单）
        assert sync.path_whitelisted("/api/identity/logout", "POST")
        assert sync.path_whitelisted("/api/identity/personal-access-tokens", "GET")
        assert not sync.path_whitelisted("/api/identity/user/export-async", "POST")


class TestRequiresPermission:
    def test_explicit_permission_classes(self):
        class NoPermission:
            permission_classes = []

        class AllowAnyView:
            permission_classes = [type("AllowAny", (), {})]

        class CustomOnly:
            permission_classes = [type("PatScopePermission", (), {})]

        class DefaultChain:
            permission_classes = [type("IsAuthenticated", (), {})]

        assert not sync.requires_permission(NoPermission)
        assert not sync.requires_permission(AllowAnyView)
        assert not sync.requires_permission(CustomOnly)
        assert sync.requires_permission(DefaultChain)
        assert sync.requires_permission(type("Inherited", (), {}))


class TestFindCovering:
    @staticmethod
    def _perm(path, method, name="x"):
        return SimpleNamespace(path=path, method=method, name=name)

    def test_exact_match_wins(self):
        perms = [
            self._perm("api/identity/user/(?P<pk>[^/.]+)$", "GET"),
            self._perm("api/identity/user$", "GET"),
        ]
        hit = sync.find_covering(perms, "api/identity/user", "GET")
        assert hit.path == "api/identity/user$"

    def test_regex_fallback_matches_single_segment(self):
        """运行时口径：多段路径不匹配（recycle/purge 不命中 `[^/.]+`），单段会命中。"""
        perms = [self._perm("api/system/menu/(?P<pk>[^/.]+)$", "GET")]
        assert sync.find_covering(perms, "api/system/menu/recycle", "GET") is not None
        assert sync.find_covering(perms, "api/system/menu/recycle/purge", "GET") is None

    def test_method_must_match(self):
        perms = [self._perm("api/identity/user$", "GET")]
        assert sync.find_covering(perms, "api/identity/user", "POST") is None

    def test_shared_method_path_covers_registered_methods(self):
        """登记的多方法共享端点（im-binding）：单权限点同时覆盖 GET / POST。"""
        perms = [self._perm("api/identity/user/(?P<pk>[^/.]+)/im-binding$", "GET", "imBinding:SystemUser")]
        path = "api/identity/user/(?P<pk>[^/.]+)/im-binding"
        assert sync.find_covering(perms, path, "GET") is not None
        assert sync.find_covering(perms, path, "POST") is not None
        # 未登记的方法不放宽（如实暴露缺口）
        assert sync.find_covering(perms, path, "DELETE") is None

    def test_shared_method_registry_matches_view_action(self):
        """登记表与视图 action 同源：im-binding 仍为单动作 GET+POST（防登记表悬空）。"""
        from identity.views.admin.user import UserViewSet

        action = UserViewSet.im_binding
        assert sorted(action.mapping) == ["get", "post"]
        assert f"api/identity/user/(?P<pk>[^/.]+)/{action.url_path}$" in sync.SHARED_METHOD_PATHS


class TestSeedMerge:
    def test_detect_indent(self):
        assert sync.detect_indent('[\n {\n  "model": 1\n }\n]') == 1
        assert sync.detect_indent('[\n  {\n    "model": 1\n  }\n]') == 2
        assert sync.detect_indent("[]") == 1

    def test_merge_replaces_and_appends_preserving_indent(self, tmp_path):
        seed = tmp_path / "menu.json"
        seed.write_text(
            json.dumps(
                [
                    {"model": "system.menu", "pk": "a", "fields": {"creator": 9, "modifier": 9, "name": "old"}},
                ],
                indent=1,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf8",
        )
        result = sync.merge_seed_file(
            seed,
            [
                {"model": "system.menu", "pk": "a", "fields": {"creator": 2, "modifier": 2, "name": "new"}},
                {"model": "system.menu", "pk": "b", "fields": {"creator": 2, "modifier": 2, "name": "added"}},
            ],
        )
        assert result["updated"] == 1 and result["added"] == 1

        data = json.loads(seed.read_text(encoding="utf8"))
        assert [item["pk"] for item in data] == ["a", "b"]
        # creator/modifier 归一为 1（种子纪律：新库首个超管）
        assert data[0]["fields"]["creator"] == 1
        assert data[0]["fields"]["name"] == "new"
        # 缩进保持 1（与既有种子文件一致，避免整文件重排）
        assert seed.read_text(encoding="utf8").startswith("[\n {")


@pytest.mark.django_db
class TestBuildPlansFallback:
    def test_unresolved_without_parent(self):
        route = SimpleNamespace(
            view="unknown.ViewSet",
            name="unknown",
            url="api/unknown/thing$",
            sample="/api/unknown/thing",
            actions={"get": "list"},
            view_cls=None,
            requires_permission=True,
        )
        plans, unresolved = sync.build_plans([(route, "GET", "list")], [route], [])
        assert plans == []
        assert len(unresolved) == 1

    def test_default_parent_produces_fallback_code(self):
        from system.models import Menu, MenuMeta

        meta = MenuMeta.objects.create(title="父菜单")
        parent = Menu.objects.create(
            name="ParentPage", path="/parent/index", menu_type=Menu.MenuChoices.MENU, meta=meta
        )
        route = SimpleNamespace(
            view="unknown.ViewSet",
            name="unknown",
            url="api/unknown/thing$",
            sample="/api/unknown/thing",
            actions={"get": "list"},
            view_cls=None,
            requires_permission=True,
        )
        plans, unresolved = sync.build_plans([(route, "GET", "list")], [route], [], default_parent=parent)
        assert not unresolved
        assert len(plans) == 1
        assert plans[0].code == "list:ParentPage"
        assert plans[0].parent_id == parent.pk


@pytest.mark.django_db
class TestGrantToRoles:
    """``--grant-roles`` 只在「同模块 + 父菜单」范围内补授，不静默扩大授权。"""

    @staticmethod
    def _menu(name, menu_type, parent=None, path=""):
        from system.models import Menu, MenuMeta

        meta = MenuMeta.objects.create(title=name)
        return Menu.objects.create(name=name, menu_type=menu_type, path=path, parent=parent, meta=meta)

    @staticmethod
    def _role(code):
        from identity.models import UserRole

        return UserRole.objects.create(name=code, code=code)

    def test_parentless_permission_is_not_granted(self):
        """父菜单为空时不得授予「持有任一顶层权限点」的角色。"""
        from system.models import Menu

        top = self._menu("list:TopLevel", Menu.MenuChoices.PERMISSION)
        created = self._menu("list:NewThing", Menu.MenuChoices.PERMISSION)
        role = self._role("holder-of-top")
        role.menu.add(top)

        granted = sync.grant_to_roles([created], [top, created], {created.pk: None})

        assert granted == []
        assert not role.menu.filter(pk=created.pk).exists()

    def test_granted_only_to_roles_holding_parent_and_sibling(self):
        from system.models import Menu

        parent = self._menu("PageParent", Menu.MenuChoices.MENU, path="/parent/index")
        sibling = self._menu("list:Sibling", Menu.MenuChoices.PERMISSION, parent=parent, path="api/x/sibling$")
        created = self._menu("list:NewThing", Menu.MenuChoices.PERMISSION, parent=parent, path="api/x/new$")

        role = self._role("has-parent-and-sibling")
        role.menu.add(parent, sibling)
        role_without_parent = self._role("has-sibling-only")
        role_without_parent.menu.add(sibling)

        granted = sync.grant_to_roles([created], [sibling, created], {created.pk: parent.pk})

        assert [name for name, _menu_name in granted] == ["has-parent-and-sibling"]
        assert role.menu.filter(pk=created.pk).exists()
        assert not role_without_parent.menu.filter(pk=created.pk).exists()


@pytest.mark.django_db
class TestStrictGate:
    """``--strict`` 在 apply 模式（不带 --dry-run）同样判定孤儿权限点。"""

    @staticmethod
    def _options(**overrides):
        options = {
            "dry_run": False,
            "skip_fields": True,
            "skip_permissions": False,
            "grant_roles": False,
            "update_seed": False,
            "refresh_titles": False,
            "fix_field_permissions": False,
            "default_parent": "",
            "strict": True,
            "prune": False,
        }
        options.update(overrides)
        return options

    def _patch_scan(self, monkeypatch, orphans):
        monkeypatch.setattr(sync, "build_route_index", lambda: [])
        monkeypatch.setattr(sync, "unclassified_views", lambda routes: [])
        monkeypatch.setattr(sync, "load_permission_menus", lambda: [])
        monkeypatch.setattr(sync, "scan_gaps", lambda routes, perms: [])
        monkeypatch.setattr(sync, "build_plans", lambda *args, **kwargs: ([], []))
        monkeypatch.setattr(sync, "plan_binding_fixes", lambda *args, **kwargs: [])
        monkeypatch.setattr(sync, "audit_permission_menus", lambda *args, **kwargs: (orphans, [], [], []))
        monkeypatch.setattr(sync, "audit_wide_manager_grants", lambda: [])
        monkeypatch.setattr(sync, "audit_field_permissions", lambda: [])
        monkeypatch.setattr(sync, "apply_plans", lambda plans, user: ([], [], []))
        monkeypatch.setattr(sync, "apply_binding_fixes", lambda fixes: 0)

    def test_strict_fails_in_apply_mode(self, monkeypatch):
        from system.management.commands.sync_menu_permissions import Command

        orphan = SimpleNamespace(pk="orphan-pk", name="list:Orphan", path="api/orphan$", method="GET")
        self._patch_scan(monkeypatch, [orphan])
        with pytest.raises(SystemExit):
            Command()._sync_permissions(self._options(), None, False)

    def test_strict_passes_when_no_orphans(self, monkeypatch):
        from system.management.commands.sync_menu_permissions import Command

        self._patch_scan(monkeypatch, [])
        Command()._sync_permissions(self._options(), None, False)


@pytest.mark.django_db
class TestPruneOrphans:
    def test_prune_physically_removes_menu_and_meta(self):
        from system.management.commands.sync_menu_permissions import Command
        from system.models import Menu, MenuMeta

        meta = MenuMeta.objects.create(title="孤儿权限点")
        orphan = Menu.objects.create(
            name="list:OrphanPrune", menu_type=Menu.MenuChoices.PERMISSION, path="api/orphan$", meta=meta
        )
        Command()._prune_permissions([orphan])

        assert not Menu.all_objects.filter(pk=orphan.pk).exists()
        assert not MenuMeta.objects.filter(pk=meta.pk).exists()
