#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""轨迹启发式校验（滑块 / 点选共用）。

目标不是识别真人（那需要行为风控模型），而是把「零成本脚本直接 POST 一个
坐标」挡在门外：要求至少 2 个拖动采样点（点选为 3 个点击点）、总时长落在
人类可完成的区间、相邻采样不出现瞬移。所有规则失败都只返回 False，不向
客户端暴露具体原因（防探测）。
"""

import math
from itertools import pairwise
from typing import Any

#: 单次挑战允许的最短 / 最长操作时长（毫秒）。
#: 上限放宽到 60s：点选题需要读提示、找字，正常用户超过 10s 很常见。
MIN_DURATION_MS = 80
MAX_DURATION_MS = 60_000
#: 相邻采样点的最大瞬时速度（像素/毫秒）：只拦「坐标瞬移」类脚本。
#: 50 px/ms = 50000 px/s，正常快速拖动（含事件合并）远低于此值。
MAX_SPEED_PX_PER_MS = 50.0


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _parse_point(item: Any) -> tuple[float, float, float] | None:
    if not isinstance(item, dict):
        return None
    x, y, t = _number(item.get("x")), _number(item.get("y")), _number(item.get("t"))
    if x is None or y is None or t is None:
        return None
    return x, y, t


def validate_track(track: Any, min_points: int = 2) -> bool:
    """轨迹是否像一次人类操作；``track`` 缺失时由调用方决定是否强制。"""
    if not isinstance(track, list) or len(track) < min_points:
        return False
    points = [_parse_point(item) for item in track]
    if any(point is None for point in points):
        return False
    parsed = [point for point in points if point is not None]

    for previous, current in pairwise(parsed):
        if current[2] < previous[2]:
            return False
        distance = math.hypot(current[0] - previous[0], current[1] - previous[1])
        elapsed = max(current[2] - previous[2], 1.0)
        if distance / elapsed > MAX_SPEED_PX_PER_MS:
            return False

    duration = parsed[-1][2] - parsed[0][2]
    return MIN_DURATION_MS <= duration <= MAX_DURATION_MS
