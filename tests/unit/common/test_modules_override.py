# -*- coding: utf-8 -*-
"""功能模块后台覆盖层（DB 单行）单元测试。

守护两条核心语义：

1. **优先级**：覆盖行存在时整体替换部署基线（config.yml / 环境变量）；
2. **不热更新**：写入覆盖行不得改变当前进程的生效态，只有重启（reset_module_state
   模拟）后才生效——否则等同于运行期热更新，与装配期语义冲突。
"""

import pytest

from common.core.modules import (
    clear_override,
    desired_modules,
    module_diff,
    override_active,
    preview_modules,
    reset_module_state,
    resolve_modules,
)

pytestmark = pytest.mark.django_db


class TestOverridePrecedence:
    def test_baseline_used_when_no_override(self, module_config):
        module_config(preset="standard")

        assert override_active() is False
        assert resolve_modules().preset == "standard"

    def test_override_wins_over_baseline(self, module_config, module_override):
        module_config(preset="standard")
        module_override(preset="full")

        assert override_active() is True
        assert resolve_modules().preset == "full"
        assert resolve_modules().is_full

    def test_override_replaces_baseline_overrides(self, module_config, module_override):
        """覆盖行整体替换基线：基线里的 MODULE_DISABLE 不再叠加。"""

        module_config(preset="full", disable=["chat"])
        assert "chat" in resolve_modules().disabled

        module_override(preset="full", enable=[], disable=[])

        assert resolve_modules().disabled == frozenset()


class TestOverrideIsFailSafe:
    def test_load_override_swallows_db_error(self, monkeypatch, module_config):
        from common.core.modules import override as override_module

        module_config(preset="standard")

        def boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(override_module, "_model", boom)

        assert override_module.load_override() is None
        reset_module_state()
        # 读不到覆盖行时回退部署基线，不影响启动
        assert resolve_modules().preset == "standard"


class TestNoHotReload:
    def test_desired_reflects_save_without_changing_effective(self, module_override):
        reset_module_state()
        effective_before = resolve_modules().preset

        module_override(preset="standard", reset=False)

        # 生效态仍是进程启动时的解析结果
        assert resolve_modules().preset == effective_before
        # 待生效态立刻反映刚写入的覆盖行（不能被缓存）
        assert desired_modules().preset == "standard"

    def test_restart_applies_override(self, module_override):
        module_override(preset="standard")

        assert resolve_modules().preset == "standard"

    def test_clear_override_restores_baseline_after_restart(self, module_config, module_override):
        module_config(preset="standard")
        module_override(preset="full")
        assert resolve_modules().preset == "full"

        clear_override()
        reset_module_state()

        assert override_active() is False
        assert resolve_modules().preset == "standard"


class TestModuleDiff:
    def test_diff_reports_added_removed_and_preset(self):
        effective = preview_modules(preset="full", enable=(), disable=())
        desired = preview_modules(preset="standard", enable=(), disable=())

        diff = module_diff(effective, desired)

        assert diff["preset_changed"] is True
        assert diff["enable"] == []
        assert "chat" in diff["disable"]

    def test_diff_is_empty_for_same_resolution(self):
        effective = preview_modules(preset="full", enable=(), disable=())
        desired = preview_modules(preset="full", enable=(), disable=())

        assert module_diff(effective, desired) == {"enable": [], "disable": [], "preset_changed": False}
