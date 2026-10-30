# -*- coding: utf-8 -*-
"""内核 Registry 原语与三处扩展点（biz 渲染器 / AI 动作 / 通知渠道）的注册与重置。"""

import pytest

from common.core.registry import Registry


class TestRegistryPrimitive:
    def test_builtin_plus_extra_lookup(self):
        reg = Registry("test", {"a": 1})
        reg.register("b", 2)
        assert reg.get("a") == 1 and reg.get("b") == 2 and reg.get("x") is None
        assert set(reg.keys()) == {"a", "b"} and len(reg) == 2 and "b" in reg

    def test_duplicate_keys_fail_fast(self):
        reg = Registry("test", {"a": 1})
        reg.register("b", 2)
        with pytest.raises(ValueError, match="already declared"):
            reg.register("b", 3)  # 扩展冲突
        with pytest.raises(ValueError, match="already declared"):
            reg.register("a", 9)  # 内置冲突
        reg.register("a", 9, override=True)  # 显式覆盖放行
        assert reg.get("a") == 9

    def test_reset_restores_builtin_only(self):
        reg = Registry("test", {"a": 1})
        reg.register("b", 2)
        reg.reset()
        assert reg.get("b") is None and reg.get("a") == 1 and len(reg) == 1


class TestBizRendererRegistry:
    def test_register_biz_renderer_extends_summary(self, db):
        from approval.services.approval_flow.biz import RENDERERS, biz_summary, register_biz_renderer

        class _Inst:
            biz_type = "unit_biz"
            biz_id = "1"

        assert biz_summary(_Inst())["missing"] is True  # 未注册 → 不可达

        try:

            @register_biz_renderer("unit_biz")
            def _render(instance):
                return {
                    "type": "unit_biz",
                    "label": "单测",
                    "title": "t",
                    "status": "s",
                    "fields": [],
                    "missing": False,
                }

            assert biz_summary(_Inst())["title"] == "t"
            with pytest.raises(ValueError, match="already declared"):
                register_biz_renderer("unit_biz")(_render)  # 键冲突 fail-fast
        finally:
            RENDERERS.reset()
        assert biz_summary(_Inst())["missing"] is True  # reset 回内置态


class TestAiActionRegistration:
    def test_register_action_visible_and_resettable(self):
        from ai.services.actions.ai_registry import action_specs, register_action, reset_action_specs_cache

        reset_action_specs_cache()
        try:
            register_action({"key": "unit.ping", "label": "ping"})
            assert "unit.ping" in action_specs()
            with pytest.raises(Exception, match="重复"):
                register_action({"key": "unit.ping", "label": "again"})
        finally:
            reset_action_specs_cache()
        assert "unit.ping" not in action_specs()

    def test_register_action_requires_key(self):
        from ai.services.actions.ai_registry import register_action, reset_action_specs_cache

        reset_action_specs_cache()
        try:
            with pytest.raises(Exception, match="key"):
                register_action({"label": "no-key"})
        finally:
            reset_action_specs_cache()


class TestNotificationChannelRegistration:
    """渠道枚举（BACKEND）与内置 CHANNEL_SPECS 是双射（守护见 test_channel_specs）：
    运行期登记面向「枚举成员已存在、spec 后补」与二开渠道，可测面是冲突/校验 fail-fast 与重置。"""

    def test_register_channel_fail_fast_paths(self):
        from notifications.backends.specs import ChannelSpec, reset_channel_specs
        from notifications.registry import register_channel

        reset_channel_specs()
        try:
            # 已有内置渠道 code：冲突 fail-fast
            with pytest.raises(ValueError, match="already declared"):
                register_channel(ChannelSpec(code="email", module="email", renderer="get_email_msg"))
            # 渲染方法不存在：fail-fast（不允许静默回退公共渲染）
            with pytest.raises(ValueError, match="missing renderer"):
                register_channel(ChannelSpec(code="email", module="email", renderer="nope"))
            # 枚举外 code：fail-fast（渠道枚举是序列化契约）
            with pytest.raises(ValueError, match="not in BACKEND"):
                register_channel(ChannelSpec(code="no_such", module="x", renderer="get_text_msg"))
        finally:
            reset_channel_specs()

    def test_all_specs_equal_builtin_without_extras(self):
        from notifications.backends.specs import CHANNEL_SPECS, all_channel_specs, reset_channel_specs

        assert all_channel_specs() == CHANNEL_SPECS  # 无扩展时等于内置清单
        reset_channel_specs()
        assert all_channel_specs() == CHANNEL_SPECS

    def test_html_backends_follows_live_specs(self):
        """HTML 渠道集合从活 spec 清单计算（内置 + 扩展），不再是导入期冻结常量。"""
        from notifications.backends import BACKEND
        from notifications.backends.specs import reset_channel_specs
        from notifications.notifications import html_message_backends

        reset_channel_specs()
        assert BACKEND.EMAIL in html_message_backends()
        assert BACKEND.SITE_MSG in html_message_backends()
        assert BACKEND.SMS not in html_message_backends()  # 短信渠道非 html
