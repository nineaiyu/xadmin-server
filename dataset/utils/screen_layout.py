#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""大屏画布窗格（``Screen.layout``）的规范化与校验。

批次一把大屏从「仪表盘轮播」扩为「画布布局」，两种形态共存：

- ``layout`` 为空 → 维持既有轮播行为（存量数据零影响，前端回退链的判定点）；
- ``layout`` 非空 → 按窗格绝对定位渲染（12 列栅格，行高由前端定义）。

本模块是窗格载荷的**单一事实源**：序列化器直接调用 ``normalize_screen_layout``，
归一化（丢弃未声明键、统一类型）与校验（越界 / 重叠 / 未知仪表盘 / 数量上限）一起做，
避免「写入端能存、读取端解析不了」的脏数据落库。

坐标系：``x`` 为列下标（0 起，``x + w <= 12``），``y`` 为行下标（0 起，``y + h <= 60``）。
重叠一律拒绝：栅格画布上重叠几乎总是误操作，且会让「谁在上层」变成隐式行为。
"""

from typing import Any

from django.utils.translation import gettext_lazy as _

#: 允许的窗格类型：仪表盘 / 文本 / 时钟 / 指标卡 / 图片
SCREEN_PANE_TYPES = ("dashboard", "text", "clock", "metric", "image")
#: 栅格列数（与前端 grid-template-columns 一致）
SCREEN_GRID_COLS = 12
#: 行数上限：够大屏纵向堆叠，同时挡住无意义的天文数字
SCREEN_MAX_ROWS = 60
#: 窗格数量上限
SCREEN_MAX_PANES = 24
#: 文本窗格内容长度上限
SCREEN_MAX_TEXT = 2000
#: 窗格标题长度上限
SCREEN_MAX_TITLE = 64
#: 窗格标识长度上限（前端生成，仅用于定位）
SCREEN_MAX_PANE_ID = 64
#: 字号范围：大屏远距离阅读，低于 14 无意义；上限放宽到 200（标语 / 时钟等大幅文字）
SCREEN_MIN_FONT_SIZE = 14
SCREEN_MAX_FONT_SIZE = 200
#: 指标卡窗格的聚合口径（与数据集聚合 ALLOWED_METRICS 同口径）
SCREEN_METRIC_TYPES = ("count", "sum", "avg")
#: 图片窗格的填充方式
SCREEN_IMAGE_FITS = ("cover", "contain", "fill")
#: 图片地址长度上限
SCREEN_MAX_IMAGE_URL = 512


class ScreenLayoutError(ValueError):
    """窗格载荷非法（消息可直接回给调用方）。"""


def boxes_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """两个窗格是否在栅格上重叠（边界相接不算重叠）。"""
    return not (
        first["x"] + first["w"] <= second["x"]
        or second["x"] + second["w"] <= first["x"]
        or first["y"] + first["h"] <= second["y"]
        or second["y"] + second["h"] <= first["y"]
    )


def _as_int(value: Any, field: str) -> int:
    # bool 是 int 的子类：True 会被静默当成 1，必须显式拒绝
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScreenLayoutError(_("Invalid screen layout box: {}").format(field))
    return value


def normalize_screen_layout(raw: Any, dashboard_pks: Any, dataset_pks: Any = ()) -> list[Any]:
    """归一化并校验窗格列表；非法即抛 ``ScreenLayoutError``（消息可读）。

    ``dataset_pks``：指标卡窗格引用的数据集白名单（pk 字符串集合/序列），
    不传则跳过存在性校验（兼容仅做结构校验的调用方，如单测）。
    """
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ScreenLayoutError(_("Invalid screen layout"))

    known_dashboards = {str(pk) for pk in dashboard_pks}
    known_datasets = {str(pk) for pk in (dataset_pks or ())}
    normalised: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ScreenLayoutError(_("Invalid screen layout pane: {}").format(index + 1))

        pane_type = str(item.get("type") or "dashboard")
        if pane_type not in SCREEN_PANE_TYPES:
            raise ScreenLayoutError(_("Unknown screen pane type: {}").format(pane_type))

        pane_id = str(item.get("pk") or "").strip()
        if not pane_id or len(pane_id) > SCREEN_MAX_PANE_ID:
            raise ScreenLayoutError(_("Invalid screen pane id: {}").format(index + 1))

        x = _as_int(item.get("x"), "x")
        y = _as_int(item.get("y"), "y")
        w = _as_int(item.get("w"), "w")
        h = _as_int(item.get("h"), "h")
        if x < 0 or y < 0:
            raise ScreenLayoutError(_("Screen pane position cannot be negative: {}").format(pane_id))
        if w < 1 or h < 1:
            raise ScreenLayoutError(_("Screen pane size must be positive: {}").format(pane_id))
        if x + w > SCREEN_GRID_COLS:
            raise ScreenLayoutError(_("Screen pane exceeds the grid width: {}").format(pane_id))
        if y + h > SCREEN_MAX_ROWS:
            raise ScreenLayoutError(_("Screen pane exceeds the grid height: {}").format(pane_id))

        pane = {"pk": pane_id, "type": pane_type, "x": x, "y": y, "w": w, "h": h}

        title = str(item.get("title") or "").strip()
        if len(title) > SCREEN_MAX_TITLE:
            raise ScreenLayoutError(_("Screen pane title is too long: {}").format(pane_id))
        if title:
            pane["title"] = title

        if pane_type == "dashboard":
            dashboard_pk = str(item.get("dashboard") or "").strip()
            if not dashboard_pk or dashboard_pk not in known_dashboards:
                raise ScreenLayoutError(_("Unknown dashboard in pane: {}").format(pane_id))
            pane["dashboard"] = dashboard_pk

        if pane_type == "text":
            text = str(item.get("text") or "")
            if len(text) > SCREEN_MAX_TEXT:
                raise ScreenLayoutError(_("Screen pane text is too long: {}").format(pane_id))
            pane["text"] = text
            align = str(item.get("align") or "left")
            if align not in ("left", "center", "right"):
                raise ScreenLayoutError(_("Invalid screen pane align: {}").format(pane_id))
            pane["align"] = align

        if pane_type in ("text", "clock"):
            # 字号：14~200px（时钟 / 标语等大幅文字需要更大的字号空间）
            size = item.get("size", 24 if pane_type == "text" else 40)
            size = _as_int(size, "size")
            if not SCREEN_MIN_FONT_SIZE <= size <= SCREEN_MAX_FONT_SIZE:
                raise ScreenLayoutError(
                    _("Screen pane font size must be {}~{}: {}").format(
                        SCREEN_MIN_FONT_SIZE, SCREEN_MAX_FONT_SIZE, pane_id
                    )
                )
            pane["size"] = size

        if pane_type == "metric":
            dataset_pk = str(item.get("dataset") or "").strip()
            if not dataset_pk or (known_datasets and dataset_pk not in known_datasets):
                raise ScreenLayoutError(_("Unknown dataset in pane: {}").format(pane_id))
            metric = str(item.get("metric") or "count").strip()
            if metric not in SCREEN_METRIC_TYPES:
                raise ScreenLayoutError(_("Invalid screen pane metric: {}").format(pane_id))
            value_field = str(item.get("value_field") or "").strip()
            if metric in ("sum", "avg") and not value_field:
                raise ScreenLayoutError(_("Value field is required for {} in {}").format(metric, pane_id))
            if len(value_field) > 128:
                raise ScreenLayoutError(_("Invalid screen pane value field: {}").format(pane_id))
            pane["dataset"] = dataset_pk
            pane["metric"] = metric
            if value_field:
                pane["value_field"] = value_field

        if pane_type == "image":
            url = str(item.get("url") or "").strip()
            if not url or len(url) > SCREEN_MAX_IMAGE_URL:
                raise ScreenLayoutError(_("Invalid screen pane image url: {}").format(pane_id))
            # http(s) 绝对地址或站内根相对路径（/ 开头，站内静态资源）；必须拒绝
            # // 开头的 protocol-relative 地址（等价可跳任意 host 的注入向量），
            # javascript:/data: 等其他协议同样不收
            allowed = url.startswith(("https://", "http://")) or (url.startswith("/") and not url.startswith("//"))
            if not allowed:
                raise ScreenLayoutError(
                    _("Screen pane image url must be http(s) or a root-relative path: {}").format(pane_id)
                )
            fit = str(item.get("fit") or "cover").strip()
            if fit not in SCREEN_IMAGE_FITS:
                raise ScreenLayoutError(_("Invalid screen pane image fit: {}").format(pane_id))
            pane["url"] = url
            pane["fit"] = fit

        for placed in normalised:
            if boxes_overlap(placed, pane):
                raise ScreenLayoutError(_("Screen panes overlap: {}").format(pane_id))
        normalised.append(pane)

    if len(normalised) > SCREEN_MAX_PANES:
        raise ScreenLayoutError(_("Too many screen panes (max {})").format(SCREEN_MAX_PANES))
    return normalised
