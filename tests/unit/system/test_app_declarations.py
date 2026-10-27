# -*- coding: utf-8 -*-
"""app 侧权限同步声明的守护：发现、合并冲突 fail-fast 与迁移对等性。"""

import pytest
from django.apps import apps as django_apps
from django.core.exceptions import ImproperlyConfigured

from common.core.declarations import AppDeclarations, discover_app_declarations, merge_app_declarations
from system.services import permission_sync as sync


class TestDiscovery:
    def test_declaring_apps_found_and_labeled(self):
        discovered = discover_app_declarations()
        labels = {decl.app for decl in discovered}
        # 迁移到 {app}/declarations.py 的 11 个声明方
        assert labels >= {
            "demo",
            "message",
            "ai",
            "approval",
            "identity",
            "dataset",
            "notifications",
            "settings",
            "system",
            "task",
            "audit",
        }
        # app 字段必须回填为真实 app label（声明文件里缺省时由发现期补齐）
        installed = {config.label for config in django_apps.get_app_configs()}
        assert labels <= installed

    def test_package_reexports_match_merged_view(self):
        merged = merge_app_declarations(discover_app_declarations())
        assert sync.SKIP_ROUTE_PREFIXES == merged.skip_route_prefixes
        assert sync.PARENT_MENU_MAP == merged.parent_menu_map
        assert sync.AUDIT_SKIP_PREFIXES == merged.audit_skip_prefixes
        assert sync.AUDIT_KNOWN_DUPLICATES == frozenset(merged.audit_known_duplicates)
        assert sync.SHARED_METHOD_PATHS == merged.shared_method_paths
        assert sync.RUNTIME_PERMISSION_PREFIXES == merged.runtime_permission_pk_prefixes
        assert sync.PERMISSION_EXEMPT_VIEWS == merged.permission_exempt_views


class TestMerge:
    def test_empty_discovery_merges_to_empty(self):
        merged = merge_app_declarations(())
        assert merged == AppDeclarations()

    def test_duplicate_sequence_entry_fails_fast(self):
        with pytest.raises(ImproperlyConfigured, match="skip_route_prefixes"):
            merge_app_declarations(
                (
                    AppDeclarations(app="a", skip_route_prefixes=("api/x/",)),
                    AppDeclarations(app="b", skip_route_prefixes=("api/x/",)),
                )
            )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("audit_skip_prefixes", ("api/x/",)),
            ("audit_known_duplicates", (("api/x$", "GET"),)),
            ("runtime_permission_pk_prefixes", ("6eed",)),
        ],
    )
    def test_duplicate_sequence_fields_fail_fast(self, field, value):
        with pytest.raises(ImproperlyConfigured, match=field):
            merge_app_declarations(
                (
                    AppDeclarations(app="a", **{field: value}),
                    AppDeclarations(app="b", **{field: value}),
                )
            )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("shared_method_paths", {"api/x$": ("GET", "POST")}),
            ("parent_menu_map", {"api/x": "SomeMenu"}),
            ("permission_exempt_views", {"x.views.Y": "自管"}),
        ],
    )
    def test_duplicate_mapping_fields_fail_fast(self, field, value):
        with pytest.raises(ImproperlyConfigured, match=field):
            merge_app_declarations(
                (
                    AppDeclarations(app="a", **{field: value}),
                    AppDeclarations(app="b", **{field: value}),
                )
            )


class TestMigrationParity:
    """聚合视图与迁移前 permission_sync.constants 的清单逐项对等（防迁移丢条目）。"""

    def test_skip_route_prefixes(self):
        assert set(sync.SKIP_ROUTE_PREFIXES) == {"api/demo/"}

    def test_parent_menu_map_entries(self):
        assert sync.PARENT_MENU_MAP == {
            "api/dataset/dynamic-form-submissions": "FormMySubmission",
            "api/dataset/form-data": "FormData",
            "api/approval/approval-delegations": "SystemApprovalDelegation",
            "api/identity/posts": "SystemPost",
            "api/identity/account-risks": "SystemAccountRisk",
            "api/identity/login-policies": "SystemLoginPolicy",
            "api/notifications/message-templates": "SettingMessage",
            "api/system/codegen": "SystemCodeGen",
            "api/settings/scim": ("ScimSetting", "SettingOpenPlatform"),
            "api/settings/oauth/providers": ("OAuthProviderSetting", "SettingOpenPlatform"),
            "api/settings/notify/webhook": ("WebhookNotifySetting", "SettingChannel"),
            "api/settings/captcha/log": ("SecurityCaptchaLog", "SettingCaptcha"),
            "api/settings/captcha/stats": ("SecurityCaptchaStats", "SettingCaptcha"),
            "api/settings/captcha/risk": ("SecurityCaptchaRisk", "SettingCaptcha"),
            "api/ai/mcp-call-logs": ("McpCallLog", "AiMcpServers"),
            "api/identity/authorized-applications": ("AuthorizedApplication", "SettingOpenPlatform"),
        }

    def test_audit_skip_prefixes(self):
        assert set(sync.AUDIT_SKIP_PREFIXES) == {
            "api/chat/",
            "api-docs/",
            "api/flower/",
            "api/system/global-search",
            "api/ai/mcp",
            "api/approval/approval-instances/ongoing",
            "api/approval/approval-delegations/all",
        }

    def test_audit_known_duplicates(self):
        assert sync.AUDIT_KNOWN_DUPLICATES == {
            ("api/task/executions$", "GET"),
            ("api/audit/logs/operation$", "GET"),
            ("api/settings/basic$", "GET"),
            ("api/settings/basic$", "PATCH"),
        }

    def test_shared_method_paths(self):
        assert sync.SHARED_METHOD_PATHS == {
            "api/identity/user/(?P<pk>[^/.]+)/im-binding$": ("GET", "POST"),
            "api/chat/room/(?P<pk>[^/.]+)/members$": ("GET", "POST"),
            "api/dataset/screens/(?P<pk>[^/.]+)/command$": ("GET", "POST"),
        }

    def test_runtime_permission_prefixes(self):
        assert set(sync.RUNTIME_PERMISSION_PREFIXES) == {"6eed000c"}

    def test_permission_exempt_views(self):
        assert set(sync.PERMISSION_EXEMPT_VIEWS) == {"system.views.user.configs.ConfigsViewSet"}

    def test_framework_constants_stay_in_constants_module(self):
        from system.services.permission_sync import constants

        assert "list" in constants.FRAMEWORK_PERMISSION_ACTIONS
        assert "export_data" in constants.IMPORT_EXPORT_ACTIONS
        assert "list" in constants.CRUD_BIND_ACTIONS
        assert constants.DEAD_ENDPOINT_PREFIXES == ()


class TestDemoRuntimePkAlignment:
    """demo 声明的运行期 pk 段与 seed_demo_book 的固定 pk 段同源（改名即失败）。"""

    def test_seed_command_pks_cover_declared_prefix(self):
        from demo.declarations import RUNTIME_PERMISSION_PK_PREFIXES as declared
        from demo_seed.management.commands import seed_demo_book

        prefix = declared[0]
        assert seed_demo_book.PERMISSION_PK_PREFIX.startswith(prefix)
        assert seed_demo_book.DIR_PK.startswith(prefix)
        assert seed_demo_book.MENU_PK.startswith(prefix)
