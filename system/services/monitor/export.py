#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""监控报表导出渲染（CSV / Excel 双格式，与下载中心同口径的通用表格落盘）。

调用方组装 sheets（标题 / 表头 / 行数据），本模块只负责编码与文件名。
CSV 带 UTF-8 BOM 供 Excel 正确识别中文；Excel 走 openpyxl，datetime 一律转
本地朴素时间（USE_TZ 下 aware datetime 会触发 openpyxl 拒绝写入）。
"""

import codecs
import csv
import io
from typing import Any

from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone

from common.utils.csv_safe import escape_csv_cell
from common.utils.excel import excel_safe_value, to_xlsx

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


def _csv_row_bytes(writer: Any, buffer: io.StringIO, row: Any) -> bytes:
    buffer.seek(0)
    buffer.truncate(0)
    writer.writerow([escape_csv_cell(value) for value in row])
    return buffer.getvalue().encode()


def _iter_csv_stream(header: Any, rows: Any) -> Any:
    """CSV 分块产出：首块带 BOM，之后每行一块，全量不驻留内存。"""
    yield codecs.BOM_UTF8
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    yield _csv_row_bytes(writer, buffer, header)
    for row in rows:
        yield _csv_row_bytes(writer, buffer, row)


def _xlsx_stream_bytes(title: Any, header: Any, rows: Any) -> bytes:
    """write_only 模式装配工作簿：行只驻留当前块，最终仅保留成品字节。"""
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font

    workbook = Workbook(write_only=True)
    worksheet = workbook.create_sheet(str(title)[:31] or "Sheet1")
    header_cells = []
    for value in header:
        cell = WriteOnlyCell(worksheet, value=str(excel_safe_value(value)))
        cell.data_type = "s"
        cell.font = Font(bold=True)
        header_cells.append(cell)
    worksheet.append(header_cells)
    for row in rows:
        cells = []
        for value in row:
            value = excel_safe_value(value)
            cell = WriteOnlyCell(worksheet, value=value)
            if isinstance(value, str):
                cell.data_type = "s"
            cells.append(cell)
        worksheet.append(cells)
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def render_streaming_export(prefix: Any, title: Any, header: Any, rows: Any, file_format: Any = "csv") -> HttpResponse:
    """流式导出（单 sheet）：CSV 直接分块响应；XLSX 以 write_only 装配后一次性下发。"""
    timestamp = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M%S")
    if file_format == "xlsx":
        content = _xlsx_stream_bytes(title, header, rows)
        response = HttpResponse(content, content_type=XLSX_CONTENT_TYPE)
        filename = f"{prefix}-{timestamp}.xlsx"
    else:
        response = StreamingHttpResponse(_iter_csv_stream(header, rows), content_type=CSV_CONTENT_TYPE)
        filename = f"{prefix}-{timestamp}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
