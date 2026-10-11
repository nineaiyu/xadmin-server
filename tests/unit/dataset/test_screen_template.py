# -*- coding: utf-8 -*-
"""大屏模板（ScreenTemplate）载荷与窗格新字段（ADR-088）单测。

锁三件事：

1. 模板载荷与自由画布**同形**——复用同一份归一化（画幅缺省、窗格白名单、坐标校验），
   空模板没有复用价值，直接拒绝；
2. 窗格新字段（``rotate`` / ``animation`` / ``text_format``）走白名单：非默认落库、
   默认不下发（旧载荷字节级兼容）、非法值拒绝；
3. 模板序列化器的引用可见性口径与保存大屏一致（不可见 / 未知引用一律拒绝）。
"""

import pytest
from django.utils.translation import gettext
from rest_framework import serializers

from dataset.models.dataset import Dashboard
from dataset.serializers.analysis import ScreenTemplateSerializer
from dataset.utils.screen_layout import (
    SCREEN_FREE_DEFAULT_H,
    SCREEN_FREE_DEFAULT_W,
    SCREEN_MAX_GROUP,
    ScreenLayoutError,
    normalize_screen_template,
)

pytestmark = pytest.mark.django_db


def _free_pane(pk="p1", pane_type="text", **extra):
    pane = {"pk": pk, "type": pane_type, "x": 0, "y": 0, "w": 320, "h": 200}
    pane.update(extra)
    return pane


class TestTemplatePayload:
    def test_design_defaults_and_pane_body_normalised(self):
        layout = normalize_screen_template({"panes": [_free_pane(text="标题")]}, [])
        assert (layout["design_w"], layout["design_h"]) == (SCREEN_FREE_DEFAULT_W, SCREEN_FREE_DEFAULT_H)
        assert layout["panes"][0] == {
            "pk": "p1",
            "type": "text",
            "text": "标题",
            "align": "left",
            "size": 24,
            "x": 0,
            "y": 0,
            "w": 320,
            "h": 200,
        }

    def test_empty_template_rejected(self):
        with pytest.raises(ScreenLayoutError) as excinfo:
            normalize_screen_template({"panes": []}, [])
        assert str(excinfo.value) == gettext("Screen template needs at least one pane")

    def test_pane_beyond_design_rejected(self):
        raw = {"design_w": 1280, "design_h": 720, "panes": [_free_pane(x=1000, w=400)]}
        with pytest.raises(ScreenLayoutError):
            normalize_screen_template(raw, [])


class TestPaneExtras:
    def test_non_default_fields_kept_and_defaults_dropped(self):
        raw = {
            "panes": [
                _free_pane(rotate=45, animation="fade", text_format="rich", text="**粗**"),
                _free_pane(pk="p2", rotate=0, animation="none", text_format="plain"),
            ]
        }
        panes = normalize_screen_template(raw, [])["panes"]
        assert panes[0]["rotate"] == 45
        assert panes[0]["animation"] == "fade"
        assert panes[0]["text_format"] == "rich"
        # 默认值不下发：旧载荷（无这些键）升级后字节不变
        assert "rotate" not in panes[1]
        assert "animation" not in panes[1]
        assert "text_format" not in panes[1]

    def test_rotate_range_inclusive(self):
        assert normalize_screen_template({"panes": [_free_pane(rotate=-180)]}, [])["panes"][0]["rotate"] == -180
        assert normalize_screen_template({"panes": [_free_pane(rotate=180)]}, [])["panes"][0]["rotate"] == 180
        with pytest.raises(ScreenLayoutError):
            normalize_screen_template({"panes": [_free_pane(rotate=181)]}, [])

    @pytest.mark.parametrize(
        ("field", "value"),
        [("animation", "flash"), ("text_format", "markdown"), ("rotate", True), ("rotate", "45")],
    )
    def test_invalid_values_rejected(self, field, value):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_template({"panes": [_free_pane(**{field: value})]}, [])

    @pytest.mark.parametrize("animation", ["fade", "slide-up", "zoom-in"])
    def test_animation_whitelist_accepted(self, animation):
        panes = normalize_screen_template({"panes": [_free_pane(animation=animation)]}, [])["panes"]
        assert panes[0]["animation"] == animation

    def test_animation_duration_delay_loop_only_with_animation(self):
        """时长 / 延迟 / 循环：配了动画才落库，缺省值与 none 一律不下发。"""
        panes = normalize_screen_template(
            {
                "panes": [
                    _free_pane(
                        animation="fade",
                        animation_duration=800,
                        animation_delay=200,
                        animation_loop=True,
                    ),
                    _free_pane(pk="p2", animation="fade"),
                    # none 时静默忽略（不因「配了时长却没动画」报错）
                    _free_pane(pk="p3", animation="none", animation_duration=800, animation_loop=True),
                ]
            },
            [],
        )["panes"]
        assert panes[0]["animation_duration"] == 800
        assert panes[0]["animation_delay"] == 200
        assert panes[0]["animation_loop"] is True
        # 缺省 320ms / 0ms / 不循环 → 不下发（旧载荷字节不变）
        assert "animation_duration" not in panes[1]
        assert "animation_delay" not in panes[1]
        assert "animation_loop" not in panes[1]
        assert "animation_duration" not in panes[2]
        assert "animation_loop" not in panes[2]

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("animation_duration", -1),
            ("animation_duration", 5001),
            ("animation_delay", -1),
            ("animation_delay", 5001),
        ],
    )
    def test_animation_timing_range_guard(self, field, value):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_template({"panes": [_free_pane(animation="fade", **{field: value})]}, [])


class TestTemplateSerializer:
    def test_shared_reference_visible_without_request_context(self):
        dashboard = Dashboard.objects.create(name="共享看板", layout=[], visibility="shared")
        layout = ScreenTemplateSerializer().validate_free_layout(
            {"panes": [_free_pane(pane_type="dashboard", dashboard=str(dashboard.pk))]}
        )
        assert layout["panes"][0]["dashboard"] == str(dashboard.pk)

    def test_unknown_reference_rejected(self):
        with pytest.raises(serializers.ValidationError):
            ScreenTemplateSerializer().validate_free_layout(
                {"panes": [_free_pane(pane_type="dashboard", dashboard="00000000-0000-0000-0000-000000000000")]}
            )

    def test_empty_panes_rejected(self):
        with pytest.raises(serializers.ValidationError):
            ScreenTemplateSerializer().validate_free_layout({"panes": []})


class TestTemplateGroupValidation:
    """模板分组名（ADR-090 D4）：去空白 + 长度上限，空 = 未分组。"""

    def test_group_trimmed_and_defaulted(self):
        serializer = ScreenTemplateSerializer()
        assert serializer.validate_group("  甲组  ") == "甲组"
        assert serializer.validate_group("") == ""
        assert serializer.validate_group(None) == ""

    def test_group_at_limit_kept(self):
        value = "x" * SCREEN_MAX_GROUP
        assert ScreenTemplateSerializer().validate_group(value) == value

    def test_group_too_long_rejected(self):
        with pytest.raises(serializers.ValidationError):
            ScreenTemplateSerializer().validate_group("x" * (SCREEN_MAX_GROUP + 1))
