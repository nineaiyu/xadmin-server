# -*- coding: utf-8 -*-
"""大屏画布窗格（Screen.layout / Screen.free_layout）规范化与校验单测（单一事实源）。"""

from types import SimpleNamespace

import pytest

from dataset.utils.screen_layout import (
    SCREEN_FREE_DEFAULT_H,
    SCREEN_FREE_DEFAULT_W,
    SCREEN_FREE_MAX_PANES,
    SCREEN_FREE_MIN_PANE_SIZE,
    SCREEN_GRID_COLS,
    SCREEN_MAX_PANES,
    ScreenLayoutError,
    active_screen_panes,
    boxes_overlap,
    normalize_screen_free_layout,
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
        for url in ("javascript:alert(1)", "data:image/png;base64,xxx"):
            with pytest.raises(ScreenLayoutError):
                normalize_screen_layout(
                    [{"pk": "i1", "type": "image", "x": 0, "y": 0, "w": 3, "h": 2, "url": url}], [DASH]
                )

    def test_root_relative_url_accepted(self):
        """站内根相对路径（/ 开头）与前端守卫口径对齐：过前端校验的值不再保存必 400。"""
        raw = [{"pk": "i1", "type": "image", "x": 0, "y": 0, "w": 3, "h": 2, "url": "/media/local.png"}]
        assert normalize_screen_layout(raw, [DASH])[0]["url"] == "/media/local.png"

    def test_protocol_relative_url_rejected(self):
        """// 开头的 protocol-relative 地址等价可跳任意 host：两端口径一致拒绝。"""
        for url in ("//evil.com/x.png", "//a.b/c.png"):
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


class TestVideoAndHtmlPanes:
    """视频与自定义 HTML 窗格：只收 http(s)，缺省播放行为不下发。"""

    def test_video_non_default_fields_kept(self):
        raw = [
            {
                "pk": "v1",
                "type": "video",
                "x": 0,
                "y": 0,
                "w": 6,
                "h": 4,
                "url": "https://a.b/c.mp4",
                "poster": "https://a.b/c.jpg",
                "fit": "contain",
                "muted": False,
                "loop": False,
                "autoplay": False,
            }
        ]
        assert normalize_screen_layout(raw, [DASH]) == [
            {
                "pk": "v1",
                "type": "video",
                "x": 0,
                "y": 0,
                "w": 6,
                "h": 4,
                "url": "https://a.b/c.mp4",
                "poster": "https://a.b/c.jpg",
                "fit": "contain",
                "muted": False,
                "loop": False,
                "autoplay": False,
            }
        ]

    def test_video_defaults_dropped(self):
        """缺省（静音 + 循环 + 自动播放 + cover）不下发：旧载荷升级后字节不变。"""
        raw = [{"pk": "v1", "type": "video", "x": 0, "y": 0, "w": 6, "h": 4, "url": "/media/a.mp4"}]
        pane = normalize_screen_layout(raw, [DASH])[0]
        assert pane["url"] == "/media/a.mp4"
        assert "fit" not in pane
        assert "muted" not in pane and "loop" not in pane and "autoplay" not in pane

    @pytest.mark.parametrize("url", ["javascript:alert(1)", "//evil.com/x.mp4", "data:video/mp4;x"])
    def test_video_bad_url_rejected(self, url):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout([{"pk": "v1", "type": "video", "x": 0, "y": 0, "w": 6, "h": 4, "url": url}], [DASH])

    def test_video_bad_poster_and_fit_rejected(self):
        base = {"pk": "v1", "type": "video", "x": 0, "y": 0, "w": 6, "h": 4, "url": "https://a.b/c.mp4"}
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout([{**base, "poster": "javascript:alert(1)"}], [DASH])
        with pytest.raises(ScreenLayoutError):
            normalize_screen_layout([{**base, "fit": "stretch"}], [DASH])

    def test_html_only_accepts_http_absolute(self):
        """自定义 HTML 只收 http(s) iframe 地址（原样 HTML 与站内相对路径都不收）。"""
        ok = normalize_screen_layout(
            [{"pk": "h1", "type": "html", "x": 0, "y": 0, "w": 6, "h": 4, "url": "https://a.b/embed"}],
            [DASH],
        )
        assert ok[0]["url"] == "https://a.b/embed"
        for url in ("/local/page", "javascript:alert(1)", "//evil.com", ""):
            with pytest.raises(ScreenLayoutError):
                normalize_screen_layout(
                    [{"pk": "h1", "type": "html", "x": 0, "y": 0, "w": 6, "h": 4, "url": url}],
                    [DASH],
                )


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


def _free_pane(pk="p1", pane_type="clock", x=0, y=0, w=100, h=80, **extra):
    pane = {"pk": pk, "type": pane_type, "x": x, "y": y, "w": w, "h": h}
    pane.update(extra)
    return pane


class TestFreeLayoutNormalisation:
    def test_empty_payload_means_not_configured(self):
        """空载荷（None / "" / {}）= 未启用自由画布，归一化为空字典。"""
        assert normalize_screen_free_layout(None, [DASH]) == {}
        assert normalize_screen_free_layout("", [DASH]) == {}
        assert normalize_screen_free_layout({}, [DASH]) == {}

    def test_design_defaults_with_dashboard_pane(self):
        raw = {"panes": [_free_pane(pane_type="dashboard", dashboard=DASH, x=10, y=20, w=480, h=320)]}
        assert normalize_screen_free_layout(raw, [DASH]) == {
            "design_w": SCREEN_FREE_DEFAULT_W,
            "design_h": SCREEN_FREE_DEFAULT_H,
            "panes": [{"pk": "p1", "type": "dashboard", "dashboard": DASH, "x": 10, "y": 20, "w": 480, "h": 320}],
        }

    def test_explicit_design_kept_without_panes(self):
        """只改画幅不写窗格：保留设计尺寸（切回自由模式仍记得画幅）。"""
        assert normalize_screen_free_layout({"design_w": 3840, "design_h": 2160}, [DASH]) == {
            "design_w": 3840,
            "design_h": 2160,
            "panes": [],
        }

    def test_overlap_allowed_and_array_order_kept(self):
        """自由画布的核心能力是层叠：完全重叠允许，数组顺序即层级（后者在上）。"""
        raw = {
            "panes": [
                _free_pane(pk="p1", x=0, y=0, w=400, h=300),
                _free_pane(pk="p2", pane_type="text", x=100, y=100, w=400, h=300, text="叠在上面"),
            ]
        }
        panes = normalize_screen_free_layout(raw, [DASH])["panes"]
        assert [pane["pk"] for pane in panes] == ["p1", "p2"]
        assert panes[1]["text"] == "叠在上面"

    def test_unknown_keys_dropped(self):
        raw = {
            "design_w": 1920,
            "design_h": 1080,
            "junk": "drop-me",
            "panes": [_free_pane(z=3, junk="drop-me", title="  标题  ")],
        }
        normalised = normalize_screen_free_layout(raw, [DASH])
        assert set(normalised) == {"design_w", "design_h", "panes"}
        assert normalised["panes"][0] == {
            "pk": "p1",
            "type": "clock",
            "title": "标题",
            "x": 0,
            "y": 0,
            "w": 100,
            "h": 80,
            "size": 40,
        }

    def test_pane_body_rules_shared_with_grid(self):
        """类型字段两版同口径：时钟默认字号、文本对齐、指标数据集、图片协议。"""
        assert normalize_screen_free_layout({"panes": [_free_pane()]}, [DASH])["panes"][0]["size"] == 40
        text = _free_pane(pk="t1", pane_type="text", text="标语", align="right")
        assert normalize_screen_free_layout({"panes": [text]}, [DASH])["panes"][0]["align"] == "right"
        metric = _free_pane(pk="m1", pane_type="metric", dataset="ds-1", metric="count")
        assert normalize_screen_free_layout({"panes": [metric]}, [DASH], ["ds-1"])["panes"][0]["metric"] == "count"
        image = _free_pane(pk="i1", pane_type="image", url="/media/a.png", fit="contain")
        assert normalize_screen_free_layout({"panes": [image]}, [DASH])["panes"][0]["fit"] == "contain"

    def test_pane_body_rules_reject_same_as_grid(self):
        for raw in (
            {"panes": [_free_pane(pane_type="text", align="justify")]},
            {"panes": [_free_pane(pane_type="text", size=8)]},
            {"panes": [_free_pane(pane_type="image", url="//evil.com/x.png")]},
            {"panes": [_free_pane(pane_type="metric", dataset="ds-1", metric="sum")]},
            {"panes": [_free_pane(pane_type="iframe")]},
        ):
            with pytest.raises(ScreenLayoutError):
                normalize_screen_free_layout(raw, [DASH], ["ds-1"])


class TestFreeLayoutRejections:
    def test_design_width_out_of_range(self):
        for width in (319, 7681):
            with pytest.raises(ScreenLayoutError):
                normalize_screen_free_layout({"design_w": width}, [DASH])

    def test_design_height_out_of_range(self):
        for height in (239, 4321):
            with pytest.raises(ScreenLayoutError):
                normalize_screen_free_layout({"design_h": height}, [DASH])

    def test_design_bool_rejected(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_free_layout({"design_w": True}, [DASH])

    def test_pane_size_below_minimum(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_free_layout({"panes": [_free_pane(w=SCREEN_FREE_MIN_PANE_SIZE - 1)]}, [DASH])

    def test_negative_position(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_free_layout({"panes": [_free_pane(x=-1)]}, [DASH])

    def test_bool_is_not_int(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_free_layout({"panes": [_free_pane(y=True)]}, [DASH])

    def test_exceeds_canvas_width(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_free_layout({"design_w": 320, "panes": [_free_pane(x=300, w=100)]}, [DASH])

    def test_exceeds_canvas_height(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_free_layout({"design_h": 240, "panes": [_free_pane(y=200, h=100)]}, [DASH])

    def test_too_many_panes(self):
        raw = {
            "panes": [_free_pane(pk=f"p{i}", x=i % 40, y=i // 40, w=16, h=16) for i in range(SCREEN_FREE_MAX_PANES + 1)]
        }
        with pytest.raises(ScreenLayoutError):
            normalize_screen_free_layout(raw, [DASH])

    def test_non_dict_payload_and_non_list_panes(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_free_layout([_free_pane()], [DASH])
        with pytest.raises(ScreenLayoutError):
            normalize_screen_free_layout({"panes": {"pk": "p1"}}, [DASH])

    def test_unknown_dashboard_and_dataset_rejected(self):
        with pytest.raises(ScreenLayoutError):
            normalize_screen_free_layout({"panes": [_free_pane(pane_type="dashboard", dashboard="missing")]}, [DASH])
        with pytest.raises(ScreenLayoutError):
            normalize_screen_free_layout(
                {"panes": [_free_pane(pane_type="metric", dataset="missing")]}, [DASH], ["ds-1"]
            )


class TestActiveScreenPanes:
    """投屏形态判定（唯一实现）：自由载荷非空优先 → 栅格载荷非空 → 轮播（空）。"""

    def test_free_panes_take_priority(self):
        free = {"design_w": 1920, "design_h": 1080, "panes": [_free_pane()]}
        screen = SimpleNamespace(layout_mode="free", free_layout=free, layout=[{"pk": "g1"}])
        assert active_screen_panes(screen) == free["panes"]

    def test_free_mode_with_empty_payload_falls_back_to_grid(self):
        screen = SimpleNamespace(
            layout_mode="free", free_layout={"design_w": 1920, "design_h": 1080, "panes": []}, layout=[{"pk": "g1"}]
        )
        assert active_screen_panes(screen) == [{"pk": "g1"}]

    def test_grid_mode_ignores_free_payload(self):
        free = {"panes": [_free_pane()]}
        screen = SimpleNamespace(layout_mode="grid", free_layout=free, layout=[{"pk": "g1"}])
        assert active_screen_panes(screen) == [{"pk": "g1"}]

    def test_all_empty_means_carousel(self):
        screen = SimpleNamespace(layout_mode="grid", free_layout={}, layout=[])
        assert active_screen_panes(screen) == []
        # 属性缺失（历史调用方 / 未迁移数据）同样按轮播处理，不抛错
        assert active_screen_panes(SimpleNamespace()) == []
