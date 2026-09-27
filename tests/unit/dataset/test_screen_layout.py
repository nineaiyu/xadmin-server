# -*- coding: utf-8 -*-
"""大屏画布窗格（Screen.layout）规范化与校验单测（单一事实源）。"""

import pytest

from dataset.utils.screen_layout import (
    SCREEN_GRID_COLS,
    SCREEN_MAX_PANES,
    ScreenLayoutError,
    boxes_overlap,
    normalize_screen_layout,
)

DASH = "dash-1"


def test_empty_layout_means_carousel_mode():
    assert normalize_screen_layout(None, [DASH]) == []
    assert normalize_screen_layout("", [DASH]) == []
    assert normalize_screen_layout([], [DASH]) == []


def test_dashboard_pane_normalised_and_unknown_keys_dropped():
    raw = [
        {
            "pk": "p1",
            "type": "dashboard",
            "dashboard": DASH,
            "x": 0,
            "y": 0,
            "w": 6,
            "h": 4,
            "title": "  销售总览  ",
            "junk": "drop-me",
        }
    ]
    assert normalize_screen_layout(raw, [DASH]) == [
        {"pk": "p1", "type": "dashboard", "x": 0, "y": 0, "w": 6, "h": 4, "title": "销售总览", "dashboard": DASH}
    ]


def test_type_defaults_to_dashboard():
    raw = [{"pk": "p1", "dashboard": DASH, "x": 0, "y": 0, "w": 12, "h": 3}]
    assert normalize_screen_layout(raw, [DASH])[0]["type"] == "dashboard"


def test_text_pane_defaults():
    raw = [{"pk": "t1", "type": "text", "x": 0, "y": 0, "w": 12, "h": 2, "text": "月度目标"}]
    pane = normalize_screen_layout(raw, [DASH])[0]
    assert pane["text"] == "月度目标"
    assert pane["align"] == "left"
    assert pane["size"] == 24


def test_clock_pane_keeps_only_box():
    raw = [{"pk": "c1", "type": "clock", "x": 9, "y": 0, "w": 3, "h": 2}]
    assert normalize_screen_layout(raw, [DASH]) == [{"pk": "c1", "type": "clock", "x": 9, "y": 0, "w": 3, "h": 2}]


class TestRejections:
    def test_unknown_type(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout([{"pk": "p1", "type": "iframe", "x": 0, "y": 0, "w": 6, "h": 3}], [DASH])

    def test_missing_pane_id(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout([{"pk": "  ", "type": "clock", "x": 0, "y": 0, "w": 3, "h": 2}], [DASH])

    def test_bool_is_not_int(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout(
                [{"pk": "p1", "type": "clock", "x": True, "y": 0, "w": 3, "h": 2}],
                [DASH],
            )

    def test_negative_position(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout([{"pk": "p1", "type": "clock", "x": -1, "y": 0, "w": 3, "h": 2}], [DASH])

    def test_zero_size(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout([{"pk": "p1", "type": "clock", "x": 0, "y": 0, "w": 0, "h": 2}], [DASH])

    def test_exceeds_grid_width(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout(
                [{"pk": "p1", "type": "clock", "x": SCREEN_GRID_COLS - 2, "y": 0, "w": 4, "h": 2}],
                [DASH],
            )

    def test_exceeds_grid_height(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout([{"pk": "p1", "type": "clock", "x": 0, "y": 59, "w": 3, "h": 4}], [DASH])

    def test_unknown_dashboard(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout(
                [{"pk": "p1", "type": "dashboard", "dashboard": "missing", "x": 0, "y": 0, "w": 6, "h": 3}],
                [DASH],
            )

    def test_dashboard_pane_requires_dashboard(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout([{"pk": "p1", "type": "dashboard", "x": 0, "y": 0, "w": 6, "h": 3}], [DASH])

    def test_text_align_and_size_bounds(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout(
                [{"pk": "t1", "type": "text", "x": 0, "y": 0, "w": 6, "h": 2, "align": "justify"}],
                [DASH],
            )
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout(
                [{"pk": "t1", "type": "text", "x": 0, "y": 0, "w": 6, "h": 2, "size": 8}],
                [DASH],
            )

    def test_text_too_long(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout(
                [{"pk": "t1", "type": "text", "x": 0, "y": 0, "w": 6, "h": 2, "text": "字" * 2001}],
                [DASH],
            )

    def test_title_too_long(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout(
                [{"pk": "p1", "type": "clock", "x": 0, "y": 0, "w": 3, "h": 2, "title": "标" * 65}],
                [DASH],
            )

    def test_non_dict_pane(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout(["p1"], [DASH])

    def test_non_list_layout(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout({"pk": "p1"}, [DASH])

    def test_too_many_panes(self):
        raw = [
            {"pk": f"p{i}", "type": "clock", "x": i % SCREEN_GRID_COLS, "y": i // SCREEN_GRID_COLS, "w": 1, "h": 1}
            for i in range(SCREEN_MAX_PANES + 1)
        ]
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout(raw, [DASH])


class TestOverlap:
    def test_edge_touching_is_allowed(self):
        first = {"x": 0, "y": 0, "w": 6, "h": 3}
        second = {"x": 6, "y": 0, "w": 6, "h": 3}
        assert boxes_overlap(first, second) is False
        raw = [
            {**first, "pk": "p1", "type": "clock"},
            {**second, "pk": "p2", "type": "clock"},
        ]
        assert len(normalize_screen_layout(raw, [DASH])) == 2

    def test_partial_overlap_rejected(self):
        raw = [
            {"pk": "p1", "type": "clock", "x": 0, "y": 0, "w": 6, "h": 3},
            {"pk": "p2", "type": "clock", "x": 3, "y": 1, "w": 6, "h": 3},
        ]
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout(raw, [DASH])

    def test_stacked_panes_rejected(self):
        """同一格完全重叠（后加的窗格压在上面）同样拒绝：栅格画布不接受隐式层级。"""
        raw = [
            {"pk": "p1", "type": "clock", "x": 0, "y": 0, "w": 6, "h": 3},
            {"pk": "p2", "type": "clock", "x": 0, "y": 0, "w": 6, "h": 3},
        ]
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout(raw, [DASH])
