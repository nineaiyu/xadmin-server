# -*- coding: utf-8 -*-
"""API 前缀平移命令守护：域前缀映射优先级、落库点位与幂等性。

命令服务于「存量库」：代码/种子面平移后，运行库里的 Menu.path、令牌 scopes、
审批规则 path_patterns 与系统配置 APPROVAL_REQUIRED_PATHS 仍持有旧前缀，
``migrate_api_prefixes`` 负责改写。以下用例固定映射口径与「system 内核域不动」
的边界，防止后续新增规则时误伤（如 login-policies 被 login 规则抢匹配）。
"""

import pytest
from django.core.management import call_command

from identity.models.token import PersonalAccessToken
from system.management.commands.migrate_api_prefixes import translate_text
from system.models import Menu


class TestTranslateRules:
    def test_priority_and_domain_mapping(self):
        """长前缀优先 + 四域归属：user/log 归 audit、login-policies 归 identity（不被 login 抢）。"""
        assert translate_text("api/system/user/log$") == "api/audit/user/log$"
        assert translate_text("api/system/logs/operation$") == "api/audit/logs/operation$"
        assert translate_text("api/system/mask-rules$") == "api/audit/mask-rules$"
        assert translate_text("api/system/login-policies/(?P<pk>[^/.]+)$") == (
            "api/identity/login-policies/(?P<pk>[^/.]+)$"
        )
        assert translate_text("api/system/userinfo$") == "api/identity/userinfo$"
        assert translate_text("api/system/user$") == "api/identity/user$"
        assert translate_text("api/system/file/(?P<pk>[^/.]+)$") == "api/file/file/(?P<pk>[^/.]+)$"
        assert translate_text("api/system/tasks/periodic$") == "api/task/periodic$"
        assert translate_text("api/system/exports$") == "api/task/exports$"
        assert translate_text("api/system/webhooks/subscriptions$") == "api/task/webhooks/subscriptions$"
        assert translate_text("api/system/personal-access-tokens/scope-options$") == (
            "api/identity/personal-access-tokens/scope-options$"
        )

    def test_system_kernel_paths_untouched(self):
        """system 内核域路径不在映射表内，原样保留（返回 None 表示未命中）。"""
        for value in (
            "api/system/menu$",
            "api/system/permission$",
            "api/system/dict/items$",
            "api/system/config/system$",
            "api/system/dashboard/user-total$",
            "api/system/monitor/overview$",
            "api/system/tags/assign$",
            "api/system/codegen$",
            "api/system/global-search$",
        ):
            assert translate_text(value) is None, value

    def test_regex_text_anchored_entry(self):
        """锚定正则文本（PAT scope 条目）按子串改写。"""
        assert translate_text("GET ^/api/system/user/?$") == "GET ^/api/identity/user/?$"
        assert translate_text("^(?:/api/system/role)(/.*)?$") == "^(?:/api/identity/role)(/.*)?$"


@pytest.mark.django_db
class TestCommandApply:
    def test_dry_run_reports_without_writing(self, menu_factory, capsys):
        menu = menu_factory("list:SystemUser", path="api/system/user$", method="GET")
        call_command("migrate_api_prefixes")
        menu.refresh_from_db()
        assert menu.path == "api/system/user$"
        out = capsys.readouterr().out
        assert "api/identity/user$" in out
        assert "dry-run" in out

    def test_apply_rewrites_menu_scopes_and_config(self, menu_factory, superuser):
        """落库点位：Menu.path / PAT scopes / SysConfig.APPROVAL_REQUIRED_PATHS。"""
        from common.core.config import SysConfig

        menu = menu_factory("list:SystemUser", path="api/system/user$", method="GET")
        unbound = menu_factory("list:SystemMenu", path="api/system/menu$", method="GET")
        token = PersonalAccessToken.objects.create(
            creator=superuser,
            name="probe",
            token_hash="0" * 64,
            scopes=["GET ^/api/system/user/?$", "GET ^/api/system/menu/?$"],
        )
        SysConfig.set_value("APPROVAL_REQUIRED_PATHS", ["api/system/role/"])

        call_command("migrate_api_prefixes", "--apply")

        menu.refresh_from_db()
        unbound.refresh_from_db()
        token.refresh_from_db()
        assert menu.path == "api/identity/user$"
        assert unbound.path == "api/system/menu$"
        assert token.scopes == ["GET ^/api/identity/user/?$", "GET ^/api/system/menu/?$"]
        assert SysConfig.APPROVAL_REQUIRED_PATHS == ["api/identity/role/"]

    def test_idempotent_second_run(self, menu_factory):
        menu = menu_factory("list:SystemUser", path="api/system/user$", method="GET")
        call_command("migrate_api_prefixes", "--apply")
        call_command("migrate_api_prefixes", "--apply")
        menu.refresh_from_db()
        assert menu.path == "api/identity/user$"
        assert Menu.objects.filter(path__startswith="api/system/user").count() == 0
