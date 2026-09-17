# -*- coding: utf-8 -*-
"""功能模块注册表（可裁剪架构）单元测试。

覆盖四层裁剪语义：模块解析（预设/增删/依赖校验）、路由 404 拦截、
菜单与权限点隐藏、周期任务不注册（含历史条目清理）。
"""

import json
import os

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import Client

from common.core import modules
from common.core.modules import (
    CORE,
    MODULES,
    OPTIONAL,
    STANDARD,
    disabled_route_patterns,
    filter_menu_queryset,
    is_module_enabled,
    module_signature,
    modules_report,
    resolve_modules,
)
from system.models import Menu

pytestmark = pytest.mark.django_db


class TestModuleDeclaration:
    """清单自身的一致性守护（防止声明漂移）。"""

    def test_ids_unique(self):
        ids = [spec.id for spec in MODULES]
        assert len(ids) == len(set(ids))

    def test_levels_valid(self):
        assert {spec.level for spec in MODULES} <= {CORE, STANDARD, OPTIONAL}

    def test_depends_point_to_declared_modules(self):
        ids = {spec.id for spec in MODULES}
        for spec in MODULES:
            assert set(spec.depends) <= ids, spec.id

    def test_declared_menu_names_exist_in_seed(self):
        """声明的菜单 name 必须真实存在于 loadjson/menu.json（拼错即裁剪失效）。"""
        from django.conf import settings as dj_settings

        seed = os.path.join(dj_settings.PROJECT_DIR, "loadjson", "menu.json")
        with open(seed, encoding="utf-8") as fp:
            rows = json.load(fp)
        names = {row["fields"]["name"] for row in rows if row.get("model") == "system.menu"}
        missing = {name for spec in MODULES for name in spec.menus if name not in names}
        assert not missing, f"模块声明了不存在的菜单：{sorted(missing)}"

    def test_core_modules_cannot_be_disabled_by_construction(self):
        assert all(spec.level == CORE for spec in MODULES if spec.id.startswith("core_"))


class TestModuleResolution:
    def test_default_is_full_and_nothing_disabled(self):
        resolution = resolve_modules()
        assert resolution.preset == "full"
        assert resolution.is_full
        assert not resolution.disabled
        assert disabled_route_patterns() == ()
        assert module_signature() == "full"

    def test_preset_standard_disables_optional_only(self, module_config):
        module_config(preset="standard")
        resolution = resolve_modules()
        assert resolution.is_full is False
        optional_ids = {spec.id for spec in MODULES if spec.level == OPTIONAL}
        assert resolution.disabled == frozenset(optional_ids)
        assert is_module_enabled("approval") is True
        assert is_module_enabled("chat") is False

    def test_preset_core_keeps_core_only(self, module_config):
        module_config(preset="core")
        resolution = resolve_modules()
        assert resolution.enabled == frozenset(spec.id for spec in MODULES if spec.level == CORE)
        assert is_module_enabled("datamask") is False

    def test_explicit_enable_and_disable_override_preset(self, module_config):
        module_config(preset="core", enable=["chat"], disable=["datamask"])
        assert is_module_enabled("chat") is True
        assert is_module_enabled("datamask") is False

    def test_unknown_module_rejected(self, module_config):
        module_config(disable=["not-exist"])
        with pytest.raises(ImproperlyConfigured, match="未知模块"):
            resolve_modules()

    def test_invalid_preset_rejected(self, module_config):
        module_config(preset="tiny")
        with pytest.raises(ImproperlyConfigured, match="MODULE_PRESET"):
            resolve_modules()

    def test_core_module_cannot_be_disabled(self, module_config):
        module_config(disable=["core_rbac"])
        with pytest.raises(ImproperlyConfigured, match="内核模块不可关闭"):
            resolve_modules()

    def test_dependency_must_be_enabled(self, module_config, monkeypatch):
        from dataclasses import replace

        specs = tuple(
            replace(spec, depends=("analysis",)) if spec.id == "chat" else spec for spec in modules.all_module_specs()
        )
        monkeypatch.setattr(modules, "all_module_specs", lambda: specs)
        module_config(preset="full", disable=["analysis"])
        with pytest.raises(ImproperlyConfigured, match="模块依赖未满足"):
            resolve_modules()

    def test_module_signature_reflects_combination(self, module_config):
        module_config(preset="standard")
        first = module_signature()
        assert first.startswith("standard-")
        module_config(preset="core")
        assert module_signature() != first

    def test_modules_report_lists_every_module(self, module_config):
        module_config(preset="standard")
        report = modules_report()
        assert len(report) == len(modules.all_module_specs())
        by_id = {item["id"]: item for item in report}
        assert by_id["chat"]["enabled"] is False
        assert by_id["core_rbac"]["enabled"] is True
        assert by_id["core_rbac"]["level"] == CORE


class TestRouteGate:
    def test_disabled_module_route_patterns_compiled(self, module_config):
        module_config(disable=["chat", "ai"])
        patterns = disabled_route_patterns()
        assert any(pattern.match("/api/chat/room") for pattern in patterns)
        assert any(pattern.match("/api/system/ai/profiles") for pattern in patterns)
        assert not any(pattern.match("/api/system/user") for pattern in patterns)

    def test_middleware_blocks_disabled_module_with_404(self, module_config):
        module_config(disable=["chat"])
        response = Client().get("/api/chat/room")
        assert response.status_code == 404
        assert response.json()["code"] == 1001

    def test_middleware_passes_through_other_paths(self, module_config):
        module_config(disable=["chat"])
        # 内核路径不会被模块网关拦截（未登录应为 401/403，而非模块 404）
        response = Client().get("/api/system/user")
        assert response.status_code != 404

    def test_middleware_noop_when_nothing_disabled(self):
        response = Client().get("/api/chat/room")
        assert response.status_code != 404


class TestMenuFilter:
    def test_full_preset_keeps_everything(self, module_config, menu_factory, normal_user, role):
        menu = menu_factory("Chat", menu_type=Menu.MenuChoices.MENU, path="/chat/index")
        role.menu.add(menu)
        queryset = filter_menu_queryset(Menu.objects.filter(is_active=True))
        assert menu in queryset

    def test_disabled_module_subtree_hidden(self, module_config, menu_factory, normal_user, role):
        directory = menu_factory("integration", menu_type=Menu.MenuChoices.DIRECTORY, path="/integration")
        menu = menu_factory(
            "WebhookSubscription",
            menu_type=Menu.MenuChoices.MENU,
            path="/integration/subscription/index",
            parent=directory,
        )
        perm = menu_factory(
            "list:WebhookSubscription", path="api/system/webhooks/subscriptions$", method="GET", parent=menu
        )
        sibling = menu_factory(
            "IntegrationApiApp", menu_type=Menu.MenuChoices.MENU, path="/integration/api-app/index", parent=directory
        )

        module_config(disable=["webhook"])
        queryset = filter_menu_queryset(Menu.objects.filter(is_active=True))
        assert menu not in queryset
        assert perm not in queryset
        assert sibling in queryset
        # 目录下仍有未停用子菜单（open_platform），目录本体保留
        assert directory in queryset

    def test_emptied_directory_hidden(self, module_config, menu_factory, normal_user, role):
        directory = menu_factory("formCollection", menu_type=Menu.MenuChoices.DIRECTORY, path="/form-collection")
        menu_factory(
            "FormDesigner", menu_type=Menu.MenuChoices.MENU, path="/form-collection/designer/index", parent=directory
        )
        menu_factory(
            "FormMySubmission", menu_type=Menu.MenuChoices.MENU, path="/form-collection/my/index", parent=directory
        )

        module_config(disable=["dform"])
        queryset = filter_menu_queryset(Menu.objects.filter(is_active=True))
        assert directory not in queryset

    def test_permission_prefix_hidden_for_core_directory(self, module_config, menu_factory, normal_user, role):
        """菜单树覆盖不到的权限点（如全局搜索）按 permissions 前缀隐藏。"""
        root = menu_factory("system", menu_type=Menu.MenuChoices.DIRECTORY, path="/system")
        perm = menu_factory("retrieve:SystemGlobalSearch", path="api/system/global-search$", method="GET", parent=root)
        sibling = menu_factory("SystemUser", menu_type=Menu.MenuChoices.MENU, path="/system/user/index", parent=root)

        queryset = filter_menu_queryset(Menu.objects.filter(is_active=True))
        assert perm in queryset  # 默认全开

        module_config(disable=["search"])
        queryset = filter_menu_queryset(Menu.objects.filter(is_active=True))
        assert perm not in queryset
        assert sibling in queryset
        assert root in queryset  # 目录下仍有可见子菜单，目录保留

    def test_unrelated_permission_not_hidden(self, module_config, menu_factory):
        """路由前缀推导出的权限前缀不得误伤（如 ai 前缀不得命中 api-applications）。"""
        perm = menu_factory("list:SystemUser", path="api/system/user$", method="GET")
        module_config(disable=["ai", "chat", "analysis", "webhook", "open_platform", "search"])
        assert perm in filter_menu_queryset(Menu.objects.filter(is_active=True))

    def test_user_menu_queryset_applies_module_filter(self, module_config, menu_factory, normal_user, role):
        from common.core.permission import get_user_menu_queryset

        menu = menu_factory("Chat", menu_type=Menu.MenuChoices.MENU, path="/chat/index")
        role.menu.add(menu)

        module_config(disable=["chat"])
        assert menu not in get_user_menu_queryset(normal_user)


class TestRoutesApiWithTrimmedModules:
    """走真实接口：停用模块的页面与权限码不再下发（前端菜单随之消失）。"""

    @staticmethod
    def _flatten(routes):
        paths = []
        for route in routes:
            paths.append(route.get("path"))
            paths.extend(TestRoutesApiWithTrimmedModules._flatten(route.get("children") or []))
        return paths

    @staticmethod
    def _routes(api_client):
        payload = api_client.get("/api/system/routes").json()
        return TestRoutesApiWithTrimmedModules._flatten(payload["data"]), payload["auths"]

    def test_routes_api_hides_disabled_module(self, module_config, api_client, superuser, menu_factory):
        from django.core.cache import cache

        menu_factory("Chat", menu_type=Menu.MenuChoices.MENU, path="/chat/index")
        perm = menu_factory("list:Chat", path="api/chat/room$", method="GET")
        api_client.force_authenticate(user=superuser)

        paths, auths = self._routes(api_client)
        assert "/chat/index" in paths
        assert "list:Chat" in auths

        module_config(disable=["chat"])
        # 模块组合变更需重启进程；重启时由 invalidate_trimmed_caches 完成同等清理
        cache.clear()
        paths, auths = self._routes(api_client)
        assert "/chat/index" not in paths
        assert "list:Chat" not in auths
        assert perm.pk  # 数据仍在（软裁剪不删数据）

    def test_non_superuser_routes_api_follows_module_filter(
        self, module_config, api_client, normal_user, role, menu_factory
    ):
        menu = menu_factory("Chat", menu_type=Menu.MenuChoices.MENU, path="/chat/index")
        role.menu.add(menu)
        api_client.force_authenticate(user=normal_user)

        module_config(disable=["chat"])
        paths, _auths = self._routes(api_client)
        assert "/chat/index" not in paths


class TestPeriodTaskGating:
    def _entry(self, name, module_id, task="message.tasks.clean_chat_history_job"):
        return {
            name: {
                "task": task,
                "crontab": "23 3 * * *",
                "interval": None,
                "args": (),
                "kwargs": {},
                "description": "",
                "module": module_id,
            }
        }

    def _run_registration(self, monkeypatch, entries):
        from common import tasks as common_tasks
        from common.celery import decorator as celery_decorator

        monkeypatch.setattr(celery_decorator, "get_register_period_tasks", lambda: list(entries))
        common_tasks.create_or_update_registered_periodic_tasks()

    def test_enabled_module_task_registered(self, module_config, monkeypatch):
        from django_celery_beat.models import PeriodicTask

        module_config()  # full
        self._run_registration(monkeypatch, [self._entry("unit-chat-clean", "chat")])
        assert PeriodicTask.objects.filter(name="unit-chat-clean").exists()

    def test_disabled_module_task_not_registered(self, module_config, monkeypatch):
        from django_celery_beat.models import PeriodicTask

        module_config(disable=["chat"])
        self._run_registration(monkeypatch, [self._entry("unit-chat-clean", "chat")])
        assert not PeriodicTask.objects.filter(name="unit-chat-clean").exists()

    def test_stale_entry_removed_when_module_disabled(self, module_config, monkeypatch):
        from django_celery_beat.models import PeriodicTask

        module_config()
        self._run_registration(monkeypatch, [self._entry("unit-chat-clean", "chat")])
        assert PeriodicTask.objects.filter(name="unit-chat-clean").exists()

        module_config(disable=["chat"])
        self._run_registration(monkeypatch, [self._entry("unit-chat-clean", "chat")])
        assert not PeriodicTask.objects.filter(name="unit-chat-clean").exists()

    def test_core_task_without_module_always_registered(self, module_config, monkeypatch):
        from django_celery_beat.models import PeriodicTask

        module_config(preset="core")
        entry = self._entry("unit-core-clean", None, task="common.tasks.purge_soft_deleted")
        self._run_registration(monkeypatch, [entry])
        assert PeriodicTask.objects.filter(name="unit-core-clean").exists()


class TestTrimmedCacheInvalidation:
    def test_noop_when_full(self):
        assert modules.invalidate_trimmed_caches() == 0

    def test_invalidates_when_module_disabled(self, module_config):
        module_config(disable=["chat"])
        # 不依赖被清理的键的具体数量，只要求链路可用且不抛异常
        assert modules.invalidate_trimmed_caches() >= 0
