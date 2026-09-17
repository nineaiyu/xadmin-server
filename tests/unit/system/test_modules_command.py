# -*- coding: utf-8 -*-
"""`manage.py modules`（模块清单与裁剪预演）单元测试。"""

import re
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from system.models import Menu


def run_modules(*args):
    out = StringIO()
    call_command("modules", *args, stdout=out)
    return out.getvalue()


class TestModulesCommand:
    def test_lists_current_combination(self):
        output = run_modules()
        assert "preset=full" in output
        assert "已启用 20 / 20 个模块" in output
        for module_id in ("core_rbac", "approval", "chat", "analysis"):
            assert module_id in output

    def test_preview_reports_disabled_modules(self):
        output = run_modules("--preset", "standard")
        assert "预演" in output
        assert "已启用 11 / 20 个模块" in output
        assert "停用：ai, analysis, approval_flow, chat, dform, open_platform, scim, search, webhook" in output

    def test_config_snippet_with_overrides(self):
        output = run_modules("--preset", "core", "--enable", "chat", "--config")
        assert "MODULE_PRESET: core" in output
        assert "MODULE_ENABLE:\n  - chat" in output

    def test_config_snippet_lists_disabled_override_of_preset(self):
        output = run_modules("--preset", "full", "--disable", "chat", "--config")
        assert "MODULE_DISABLE:\n  - chat" in output
        assert "MODULE_ENABLE" not in output

    def test_disable_argument_accepts_comma_separated(self):
        output = run_modules("--disable", "chat,analysis", "--config")
        assert "MODULE_DISABLE:\n  - analysis\n  - chat" in output

    def test_illegal_combination_raises_command_error(self):
        with pytest.raises(CommandError, match="内核模块不可关闭"):
            run_modules("--disable", "core_rbac")

    def test_unknown_module_raises_command_error(self):
        with pytest.raises(CommandError, match="未知模块"):
            run_modules("--disable", "not-exist")

    def test_preview_does_not_change_runtime_state(self):
        from common.core.modules import resolve_modules

        run_modules("--preset", "core")
        assert resolve_modules().is_full


@pytest.mark.django_db
class TestModulesImpact:
    """`--impact`：在某组合下、针对当前库的影响面（只读）。"""

    def test_full_reports_no_hidden(self):
        output = run_modules("--impact")

        assert "影响面：全量启用，无菜单/权限点被隐藏" in output

    def test_reports_hidden_scope_and_role_impact(self, menu_factory, role, normal_user):
        directory = menu_factory("integration", menu_type=Menu.MenuChoices.DIRECTORY, path="/integration")
        chat_page = menu_factory("Chat", menu_type=Menu.MenuChoices.MENU, path="/chat/index", parent=directory)
        chat_perm = menu_factory("list:Chat", path="api/chat/room$", method="GET", parent=chat_page)
        role.menu.add(directory, chat_page, chat_perm)

        output = run_modules("--preset", "standard", "--impact")

        # 逐模块明细：chat 的目录 1（子节点被清空）/ 页面 1 / 权限点 1 / 路由 1
        assert re.search(r"chat\s+可选\s+1\s+1\s+1\s+1", output)
        # 汇总（同一口径下的并集）
        assert "将隐藏：目录 1 / 页面 1 / 权限点 1" in output
        assert "仍可见：页面 0 / 权限点 0" in output
        # 角色影响：绑定 3 项全部命中隐藏，在册用户 1
        assert re.search(r"普通用户\s+3\s+/\s*3\s+/\s*1", output)

    def test_impact_matches_runtime_filter(self, menu_factory, module_config):
        """预演口径必须与运行期过滤一致（否则"预演说隐藏 A、实际隐藏 B"）。"""

        from common.core.modules import preview_modules
        from common.core.permission import filter_menu_queryset
        from system.models import Menu
        from system.utils.module_impact import module_impact

        directory = menu_factory("integration", menu_type=Menu.MenuChoices.DIRECTORY, path="/integration")
        menu_factory("Chat", menu_type=Menu.MenuChoices.MENU, path="/chat/index", parent=directory)
        menu_factory("list:Chat", path="api/chat/room$", method="GET", parent=directory)

        module_config(preset="standard")
        visible = set(filter_menu_queryset(Menu.objects.all()).values_list("pk", flat=True))
        runtime_hidden = [row for row in Menu.objects.all() if row.pk not in visible]

        impact = module_impact(preview_modules(preset="standard"))
        assert impact["hidden"]["pages"] == sum(1 for row in runtime_hidden if row.menu_type == Menu.MenuChoices.MENU)
        assert impact["hidden"]["permissions"] == sum(
            1 for row in runtime_hidden if row.menu_type == Menu.MenuChoices.PERMISSION
        )
        assert impact["hidden"]["directories"] == sum(
            1 for row in runtime_hidden if row.menu_type == Menu.MenuChoices.DIRECTORY
        )

    def test_impact_is_read_only(self, menu_factory, module_config):
        from common.core.modules import resolve_modules

        menu = menu_factory("Chat", menu_type=Menu.MenuChoices.MENU, path="/chat/index")

        run_modules("--preset", "standard", "--impact")

        menu.refresh_from_db()
        assert menu.is_active  # 数据未被改动
        assert resolve_modules().is_full  # 运行期状态未被改动
