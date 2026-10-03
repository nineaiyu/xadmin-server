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


def test_clock_pane_keeps_box_and_default_size():
    """时钟窗格默认字号 40（与前端历史硬编码一致）；size 显式下发时保留。"""
    raw = [{"pk": "c1", "type": "clock", "x": 9, "y": 0, "w": 3, "h": 2}]
    assert normalize_screen_layout(raw, [DASH]) == [
        {"pk": "c1", "type": "clock", "x": 9, "y": 0, "w": 3, "h": 2, "size": 40}
    ]
    raw = [{"pk": "c1", "type": "clock", "x": 9, "y": 0, "w": 3, "h": 2, "size": 96}]
    assert normalize_screen_layout(raw, [DASH])[0]["size"] == 96


class TestMetricPane:
    def test_normalised_with_dataset_and_metric(self):
        raw = [
            {
                "pk": "m1",
                "type": "metric",
                "x": 0,
                "y": 0,
                "w": 3,
                "h": 2,
                "dataset": "ds-1",
                "metric": "sum",
                "value_field": "amount",
            }
        ]
        assert normalize_screen_layout(raw, [DASH], ["ds-1"]) == [
            {
                "pk": "m1",
                "type": "metric",
                "x": 0,
                "y": 0,
                "w": 3,
                "h": 2,
                "dataset": "ds-1",
                "metric": "sum",
                "value_field": "amount",
            }
        ]

    def test_metric_defaults_to_count_without_value_field(self):
        raw = [{"pk": "m1", "type": "metric", "x": 0, "y": 0, "w": 3, "h": 2, "dataset": "ds-1", "metric": "count"}]
        pane = normalize_screen_layout(raw, [DASH], ["ds-1"])[0]
        assert pane["metric"] == "count"
        assert "value_field" not in pane

    def test_unknown_dataset_rejected(self):
        raw = [{"pk": "m1", "type": "metric", "x": 0, "y": 0, "w": 3, "h": 2, "dataset": "missing"}]
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout(raw, [DASH], ["ds-1"])

    def test_invalid_metric_rejected(self):
        raw = [{"pk": "m1", "type": "metric", "x": 0, "y": 0, "w": 3, "h": 2, "dataset": "ds-1", "metric": "median"}]
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout(raw, [DASH], ["ds-1"])

    def test_sum_requires_value_field(self):
        raw = [{"pk": "m1", "type": "metric", "x": 0, "y": 0, "w": 3, "h": 2, "dataset": "ds-1", "metric": "sum"}]
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout(raw, [DASH], ["ds-1"])


class TestImagePane:
    def test_normalised_with_url_and_fit(self):
        raw = [
            {"pk": "i1", "type": "image", "x": 0, "y": 0, "w": 3, "h": 2, "url": "https://a.b/c.png", "fit": "contain"}
        ]
        assert normalize_screen_layout(raw, [DASH]) == [
            {"pk": "i1", "type": "image", "x": 0, "y": 0, "w": 3, "h": 2, "url": "https://a.b/c.png", "fit": "contain"}
        ]

    def test_fit_defaults_to_cover(self):
        raw = [{"pk": "i1", "type": "image", "x": 0, "y": 0, "w": 3, "h": 2, "url": "https://a.b/c.png"}]
        assert normalize_screen_layout(raw, [DASH])[0]["fit"] == "cover"

    def test_missing_url_rejected(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout([{"pk": "i1", "type": "image", "x": 0, "y": 0, "w": 3, "h": 2}], [DASH])

    def test_non_http_url_rejected(self):
        for url in ("javascript:alert(1)", "data:image/png;base64,xxx", "/media/local.png"):
            with pytest.raises(ScreenLayoutError):
                normalize_screen_layout(
                    [{"pk": "i1", "type": "image", "x": 0, "y": 0, "w": 3, "h": 2, "url": url}], [DASH]
                )

    def test_invalid_fit_rejected(self):
        raw = [
            {"pk": "i1", "type": "image", "x": 0, "y": 0, "w": 3, "h": 2, "url": "https://a.b/c.png", "fit": "stretch"}
        ]
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout(raw, [DASH])


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
