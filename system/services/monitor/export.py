#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""监控报表导出渲染（CSV / Excel 双格式，与下载中心同口径的通用表格落盘）。

调用方组装 sheets（标题 / 表头 / 行数据），本模块只负责编码与文件名。
CSV 带 UTF-8 BOM 供 Excel 正确识别中文；Excel 走 openpyxl，datetime 一律转
本地朴素时间（USE_TZ 下 aware datetime 会触发 openpyxl 拒绝写入）。
"""

import csv
import io
from typing import Any

from django.utils import timezone

from common.utils.csv_safe import escape_csv_cell
from common.utils.excel import to_xlsx

CSV_CONTENT_TYPE = "text/csv; charset=utf-8"
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _csv_bytes(sheets: Any) -> Any:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    for index, sheet in enumerate(sheets):
        if index:
            writer.writerow([])
        if len(sheets) > 1:
            writer.writerow([escape_csv_cell(sheet["title"])])
        writer.writerow([escape_csv_cell(value) for value in sheet["header"]])
        for row in sheet["rows"]:
            writer.writerow([escape_csv_cell(value) for value in row])
    return buffer.getvalue().encode("utf-8-sig")


def _xlsx_bytes(sheets: Any) -> Any:
    # 装配单源走内核（表头加粗 + 列宽自适应 + 全行防公式注入 + 值收敛）
    return to_xlsx({sheet["title"]: [sheet["header"], *sheet["rows"]] for sheet in sheets})


def render_table_export(prefix: Any, sheets: Any, file_format: Any = "csv") -> Any:
    """生成导出文件，返回 (文件名, 内容 bytes, content-type)。"""
    timestamp = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M%S")
    if file_format == "xlsx":
        return f"{prefix}-{timestamp}.xlsx", _xlsx_bytes(sheets), XLSX_CONTENT_TYPE
    return f"{prefix}-{timestamp}.csv", _csv_bytes(sheets), CSV_CONTENT_TYPE
