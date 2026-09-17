# -*- coding: utf-8 -*-
"""system/utils/permission_sync.py：菜单权限点同步内核（纯逻辑 + 轻量 DB）。"""

import json
from types import SimpleNamespace

import pytest

from system.utils import permission_sync as sync


class TestUrlSampling:
    def test_url_to_sample_replaces_regex_groups(self):
        assert sync.url_to_sample("api/system/user/(?P<pk>[^/.]+)$") == "/api/system/user/1"
        assert sync.url_to_sample("api/system/monitor/task-health$") == "/api/system/monitor/task-health"
        assert sync.url_to_sample("api/system/user$") == "/api/system/user"

    def test_path_whitelisted(self):
        # 登录族在白名单内（任意方法），异步导出不在
        assert sync.path_whitelisted("/api/system/login", "POST")
        assert sync.path_whitelisted("/api/system/personal-access-tokens", "GET")
        assert not sync.path_whitelisted("/api/system/user/export-async", "POST")


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
            self._perm("api/system/user/(?P<pk>[^/.]+)$", "GET"),
            self._perm("api/system/user$", "GET"),
        ]
        hit = sync.find_covering(perms, "api/system/user", "GET")
        assert hit.path == "api/system/user$"

    def test_regex_fallback_matches_single_segment(self):
        """运行时口径：多段路径不匹配（recycle/purge 不命中 `[^/.]+`），单段会命中。"""
        perms = [self._perm("api/system/menu/(?P<pk>[^/.]+)$", "GET")]
        assert sync.find_covering(perms, "api/system/menu/recycle", "GET") is not None
        assert sync.find_covering(perms, "api/system/menu/recycle/purge", "GET") is None

    def test_method_must_match(self):
        perms = [self._perm("api/system/user$", "GET")]
        assert sync.find_covering(perms, "api/system/user", "POST") is None

    def test_shared_method_path_covers_registered_methods(self):
        """登记的多方法共享端点（im-binding）：单权限点同时覆盖 GET / POST。"""
        perms = [self._perm("api/system/user/(?P<pk>[^/.]+)/im-binding$", "GET", "imBinding:SystemUser")]
        path = "api/system/user/(?P<pk>[^/.]+)/im-binding"
        assert sync.find_covering(perms, path, "GET") is not None
        assert sync.find_covering(perms, path, "POST") is not None
        # 未登记的方法不放宽（如实暴露缺口）
        assert sync.find_covering(perms, path, "DELETE") is None

    def test_shared_method_registry_matches_view_action(self):
        """登记表与视图 action 同源：im-binding 仍为单动作 GET+POST（防登记表悬空）。"""
        from system.views.admin.user import UserViewSet

        action = UserViewSet.im_binding
        assert sorted(action.mapping) == ["get", "post"]
        assert f"api/system/user/(?P<pk>[^/.]+)/{action.url_path}$" in sync.SHARED_METHOD_PATHS


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
