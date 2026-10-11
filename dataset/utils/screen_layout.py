#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""大屏画布窗格（``Screen.layout`` / ``Screen.free_layout``）的规范化与校验。

大屏有三种形态，判定顺序见 ``active_screen_panes``（唯一实现）：

- 自由画布：``layout_mode="free"`` 且自由载荷窗格非空；
- 栅格画布：``layout`` 非空（``layout_mode="grid"``，12 列栅格，行高由前端定义）；
- 轮播：两者皆空（存量形态，零影响）。

本模块是窗格载荷的**单一事实源**：序列化器直接调用两个 ``normalize_*``，
归一化（丢弃未声明键、统一类型）与校验（越界 / 未知仪表盘 / 数量上限）一起做，
避免「写入端能存、读取端解析不了」的脏数据落库。

坐标系：

- 栅格：``x`` 为列下标（0 起，``x + w <= 12``），``y`` 为行下标（0 起，``y + h <= 60``），
  重叠一律拒绝——栅格上重叠几乎总是误操作，且会让「谁在上层」变成隐式行为；
- 自由：``x``/``y`` 为像素坐标（非负），``w``/``h`` 为像素尺寸（下限 16），
  以基准设计画幅 ``design_w x design_h`` 为界（投屏按此等比适配），
  **允许重叠**——自由画布的核心能力是层叠，层级 = 数组顺序（后者在上）。

两版只差坐标段与重叠判定：``pk`` / ``type`` / ``title`` 与类型专属字段全部经
``_normalize_pane_body`` 归一化，杜绝「同一字段在两套模式两套规则」。
"""

from typing import Any

from django.utils.translation import gettext_lazy as _

#: 允许的窗格类型：仪表盘 / 文本 / 时钟 / 指标卡 / 图片
SCREEN_PANE_TYPES = ("dashboard", "text", "clock", "metric", "image")
#: 栅格列数（与前端 grid-template-columns 一致）
SCREEN_GRID_COLS = 12
#: 行数上限：够大屏纵向堆叠，同时挡住无意义的天文数字
SCREEN_MAX_ROWS = 60
#: 窗格数量上限（栅格；自由画布元素更碎，见 SCREEN_FREE_MAX_PANES）
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

#: 自由画布模式标识（与 ``Screen.LayoutMode.FREE`` 同值；本模块刻意不依赖模型）
SCREEN_LAYOUT_MODE_FREE = "free"
#: 自由画布窗格数量上限（元素更碎，比栅格版放宽）
SCREEN_FREE_MAX_PANES = 60
#: 自由画布窗格最小尺寸（px）：再小已无法呈现内容
SCREEN_FREE_MIN_PANE_SIZE = 16
#: 基准设计画幅缺省值（px）：投屏按设计画幅等比缩放
SCREEN_FREE_DEFAULT_W = 1920
SCREEN_FREE_DEFAULT_H = 1080
#: 基准设计画幅允许范围（px）：挡住无意义的画幅（过小编辑不了 / 过大无消费场景）
SCREEN_FREE_MIN_DESIGN_W = 320
SCREEN_FREE_MAX_DESIGN_W = 7680
SCREEN_FREE_MIN_DESIGN_H = 240
SCREEN_FREE_MAX_DESIGN_H = 4320

#: 窗格旋转角度范围（度）：**视觉旋转**，不参与几何（吸附 / 框选 / 对齐仍按轴对齐盒）
SCREEN_MIN_ROTATE = -180
SCREEN_MAX_ROTATE = 180
#: 入场动画（展示态一次性播放）；none 为缺省且不下发（载荷精简 + 旧版兼容）
SCREEN_ANIMATIONS = ("none", "fade", "slide-up", "slide-down", "slide-left", "slide-right", "zoom-in")
#: 文本窗格格式：plain 缺省（不下发）/ rich（受限标记，前端先转义再套白名单渲染）
SCREEN_TEXT_FORMATS = ("plain", "rich")


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


def _normalize_pane_body(
    item: dict[str, Any],
    index: int,
    known_dashboards: set[str],
    known_datasets: set[str],
) -> dict[str, Any]:
    """窗格公共体（栅格与自由画布共用同一套字段口径）。

    返回 ``{"pk", "type", ...类型专属字段}``（不含坐标）；坐标段（栅格下标 vs 像素）
    与重叠判定由两个 ``normalize_*`` 各自处理——两版只差这两处，类型字段不会分叉。
    """
    pane_type = str(item.get("type") or "dashboard")
    if pane_type not in SCREEN_PANE_TYPES:
        raise ScreenLayoutError(_("Unknown screen pane type: {}").format(pane_type))

    pane_id = str(item.get("pk") or "").strip()
    if not pane_id or len(pane_id) > SCREEN_MAX_PANE_ID:
        raise ScreenLayoutError(_("Invalid screen pane id: {}").format(index + 1))

    pane: dict[str, Any] = {"pk": pane_id, "type": pane_type}

    title = str(item.get("title") or "").strip()
    if len(title) > SCREEN_MAX_TITLE:
        raise ScreenLayoutError(_("Screen pane title is too long: {}").format(pane_id))
    if title:
        pane["title"] = title

    # 旋转：视觉旋转（-180~180 度），0 为缺省不下发（载荷精简 + 旧版字节级兼容）
    rotate = _as_int(item.get("rotate", 0), "rotate")
    if not SCREEN_MIN_ROTATE <= rotate <= SCREEN_MAX_ROTATE:
        raise ScreenLayoutError(
            _("Screen pane rotate must be {}~{}: {}").format(SCREEN_MIN_ROTATE, SCREEN_MAX_ROTATE, pane_id)
        )
    if rotate:
        pane["rotate"] = rotate

    # 入场动画：展示态一次性播放，none 为缺省不下发
    animation = str(item.get("animation") or "none").strip() or "none"
    if animation not in SCREEN_ANIMATIONS:
        raise ScreenLayoutError(_("Invalid screen pane animation: {}").format(pane_id))
    if animation != "none":
        pane["animation"] = animation

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
        # 文本格式：plain 缺省不下发；rich = 受限标记（渲染侧先转义再套白名单）
        text_format = str(item.get("text_format") or "plain").strip() or "plain"
        if text_format not in SCREEN_TEXT_FORMATS:
            raise ScreenLayoutError(_("Invalid screen pane text format: {}").format(pane_id))
        if text_format != "plain":
            pane["text_format"] = text_format

    if pane_type in ("text", "clock"):
        # 字号：14~200px（时钟 / 标语等大幅文字需要更大的字号空间）
        size = item.get("size", 24 if pane_type == "text" else 40)
        size = _as_int(size, "size")
        if not SCREEN_MIN_FONT_SIZE <= size <= SCREEN_MAX_FONT_SIZE:
            raise ScreenLayoutError(
                _("Screen pane font size must be {}~{}: {}").format(SCREEN_MIN_FONT_SIZE, SCREEN_MAX_FONT_SIZE, pane_id)
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

    return pane


def normalize_screen_layout(raw: Any, dashboard_pks: Any, dataset_pks: Any = ()) -> list[Any]:
    """归一化并校验栅格窗格列表；非法即抛 ``ScreenLayoutError``（消息可读）。

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

        pane = _normalize_pane_body(item, index, known_dashboards, known_datasets)
        pane_id = pane["pk"]
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
        pane.update({"x": x, "y": y, "w": w, "h": h})

        for placed in normalised:
            if boxes_overlap(placed, pane):
                raise ScreenLayoutError(_("Screen panes overlap: {}").format(pane_id))
        normalised.append(pane)

    if len(normalised) > SCREEN_MAX_PANES:
        raise ScreenLayoutError(_("Too many screen panes (max {})").format(SCREEN_MAX_PANES))
    return normalised


def _as_design_size(value: Any, field: str, default: int, minimum: int, maximum: int) -> int:
    """设计画幅单边：缺省取默认值；非整数（bool 显式拒绝）或越界即拒绝。"""
    if value in (None, ""):
        return default
    size = _as_int(value, field)
    if not minimum <= size <= maximum:
        raise ScreenLayoutError(_("Screen design canvas {} must be {}~{}").format(field, minimum, maximum))
    return size


def normalize_screen_free_layout(raw: Any, dashboard_pks: Any, dataset_pks: Any = ()) -> dict[str, Any]:
    """归一化并校验自由画布载荷 ``{design_w, design_h, panes}``；非法即抛 ``ScreenLayoutError``。

    与栅格版的差异只在坐标段与重叠判定：像素坐标（非负）、尺寸下限 16px、不超设计画幅，
    且**不做重叠判定**（层级 = 数组顺序）。空载荷（``None`` / ``""`` / ``{}``）归一化为
    ``{}``（= 未启用自由画布）；``panes`` 为空列表时保留画幅（切回自由模式仍记得设计尺寸）。
    """
    if raw in (None, "", {}):
        return {}
    if not isinstance(raw, dict):
        raise ScreenLayoutError(_("Invalid screen free layout"))

    design_w = _as_design_size(
        raw.get("design_w"), "design_w", SCREEN_FREE_DEFAULT_W, SCREEN_FREE_MIN_DESIGN_W, SCREEN_FREE_MAX_DESIGN_W
    )
    design_h = _as_design_size(
        raw.get("design_h"), "design_h", SCREEN_FREE_DEFAULT_H, SCREEN_FREE_MIN_DESIGN_H, SCREEN_FREE_MAX_DESIGN_H
    )

    panes_raw = raw.get("panes")
    if panes_raw in (None, ""):
        panes_raw = []
    if not isinstance(panes_raw, list):
        raise ScreenLayoutError(_("Invalid screen free layout panes"))

    known_dashboards = {str(pk) for pk in dashboard_pks}
    known_datasets = {str(pk) for pk in (dataset_pks or ())}
    normalised: list[dict[str, Any]] = []
    for index, item in enumerate(panes_raw):
        if not isinstance(item, dict):
            raise ScreenLayoutError(_("Invalid screen layout pane: {}").format(index + 1))

        pane = _normalize_pane_body(item, index, known_dashboards, known_datasets)
        pane_id = pane["pk"]
        x = _as_int(item.get("x"), "x")
        y = _as_int(item.get("y"), "y")
        w = _as_int(item.get("w"), "w")
        h = _as_int(item.get("h"), "h")
        if x < 0 or y < 0:
            raise ScreenLayoutError(_("Screen pane position cannot be negative: {}").format(pane_id))
        if w < SCREEN_FREE_MIN_PANE_SIZE or h < SCREEN_FREE_MIN_PANE_SIZE:
            raise ScreenLayoutError(
                _("Screen pane size must be at least {}px: {}").format(SCREEN_FREE_MIN_PANE_SIZE, pane_id)
            )
        if x + w > design_w:
            raise ScreenLayoutError(_("Screen pane exceeds the canvas width: {}").format(pane_id))
        if y + h > design_h:
            raise ScreenLayoutError(_("Screen pane exceeds the canvas height: {}").format(pane_id))
        pane.update({"x": x, "y": y, "w": w, "h": h})
        normalised.append(pane)

    if len(normalised) > SCREEN_FREE_MAX_PANES:
        raise ScreenLayoutError(_("Too many screen panes (max {})").format(SCREEN_FREE_MAX_PANES))
    return {"design_w": design_w, "design_h": design_h, "panes": normalised}


def normalize_screen_template(raw: Any, dashboard_pks: Any, dataset_pks: Any = ()) -> dict[str, Any]:
    """归一化大屏模板载荷（``{design_w, design_h, panes}``，与自由画布同形）。

    模板就是「自由画布布局的可复用切片」：同一份窗格白名单、同一套画幅与坐标校验，
    故直接复用 free 归一化，只在 API 层留一个语义明确的名字——调用方（模板序列化器）
    不必引用 free 的命名，日后模板若长出独立字段也不影响两侧。
    空模板（无窗格）没有复用价值，直接拒绝。
    """
    layout = normalize_screen_free_layout(raw, dashboard_pks, dataset_pks)
    if not layout.get("panes"):
        raise ScreenLayoutError(_("Screen template needs at least one pane"))
    return layout


def active_screen_panes(screen: Any) -> list[Any]:
    """当前生效的画布窗格（服务端侧「投屏形态判定」的唯一实现）。

    自由模式且自由载荷窗格非空 → 自由窗格；否则栅格载荷非空 → 栅格窗格；皆空 → ``[]``
    （轮播）。服务端数据推送（``dataset.screen_data``）按此展开卡片；展示端按同一顺序
    判定渲染形态，避免两处漂移导致「推了帧但没人渲染」。
    """
    free = getattr(screen, "free_layout", None)
    if getattr(screen, "layout_mode", "") == SCREEN_LAYOUT_MODE_FREE and isinstance(free, dict):
        panes = free.get("panes")
        if isinstance(panes, list) and panes:
            return panes
    layout = getattr(screen, "layout", None)
    return list(layout) if isinstance(layout, list) else []
