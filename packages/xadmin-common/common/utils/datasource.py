#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""数据源接口的量级上限（下拉/选项类小集合接口的统一保护）。

「可发起流程 / 可填报表单 / 表单选项」等数据源接口按设计是小集合的全量返回、
没有分页；定义类资源增长后单次响应载荷无上限。此处给出统一上限与裁剪语义：
超出上限时只回传前 N 条并给出可读提示（业务仍可渲染），同时记 warning 日志，
便于按真实量级评估是否推进分页化改造。
"""

from typing import Any

from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)

# 数据源接口单次返回上限（条）
DATASOURCE_MAX_ROWS = 200


def limit_datasource(queryset: Any, *, name: str = "", limit: int | None = None) -> tuple[list[Any], bool]:
    """裁剪数据源查询集：返回 ``(rows, truncated)``。

    多取一条判断是否超限（不额外 COUNT）；超限时截断并记 warning 日志。
    ``limit`` 缺省取模块常量（运行期读取，便于按部署调整与测试注入）。
    """
    limit = DATASOURCE_MAX_ROWS if limit is None else limit
    rows = list(queryset[: limit + 1])
    truncated = len(rows) > limit
    if truncated:
        logger.warning("datasource %s exceeds limit %s, truncated", name or "unknown", limit)
        rows = rows[:limit]
    return rows, truncated


def truncation_detail(limit: int | None = None) -> str:
    """超限时的可读提示（列表仍返回前 N 条，提示随响应 detail 一并下发）。"""
    limit = DATASOURCE_MAX_ROWS if limit is None else limit
    return str(_("Too many items, only the first {} are returned").format(limit))
