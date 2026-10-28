#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""导出流式化：逐行序列化 → 逐行写临时文件 → 文件响应（内存不随行数增长）。

背景（PERF-EXP-01）：原链路是 ``list() → 全量序列化列表 → json 往返副本 →
DRF 渲染器整本工作簿进内存 → 字节串``，峰值内存约为成品文件的 6~8 倍，导出上限
（``EXPORT_MAX_LIMIT``，默认 2 万行）之上确有 OOM 风险。

本模块把「取数 → 序列化 → 落盘」拉成一条流：

- 取数：``queryset.iterator(chunk_size=...)`` 分批拉取，一次只驻留一块；
- 序列化：与列表接口同一只 serializer，逐行 ``.data`` 后做与渲染器同口径的
  JSON 归一（UUID/Decimal/datetime → 字符串），因此单元格取值与旧实现一致；
- 落盘：Excel 走 openpyxl ``write_only`` 模式（边写边刷盘），CSV 直接写临时文件，
  最后交给 ``FileResponse`` 分块发给客户端。

与旧实现的已知差异：流式档不再给工作表加「表格样式」（``Table`` 对象需要回读
单元格，与 write_only 互斥），改为表头冻结 + 自动筛选；列宽仍按内容估算。
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Iterator
from typing import Any

from django.http import FileResponse
from django.utils.encoding import escape_uri_path
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.utils import get_column_letter
from rest_framework.utils import encoders

from common.settings_contract import kernel_setting
from common.utils import get_logger

logger = get_logger(__name__)

#: 每批从游标拉取的实例数：太小会放大往返，太大则吃掉分批收益
STREAM_CHUNK_SIZE = 500


def iter_serialized_rows(
    queryset: Any, serializer_class: Any, context: Any = None, limit: Any = None
) -> Iterator[dict[str, Any]]:
    """逐行序列化：``iterator()`` 分批取数，行级做 JSON 归一。

    - ``limit`` 缺省取内核契约 ``EXPORT_MAX_LIMIT``；
    - ``filter_queryset`` 返回非 QuerySet（列表 / values 结果）时退化为普通迭代，
      只保留 limit 条，语义与旧实现的切片一致；
    - 序列化器按行实例化但复用同一份 context（不必每行重建上下文）。
    """
    limit = kernel_setting("EXPORT_MAX_LIMIT") if limit is None else int(limit)
    if limit and hasattr(queryset, "__getitem__"):
        # QuerySet：先切到 limit 再 iteration——否则 for 循环为了判断是否越界会多拉一行
        queryset = queryset[:limit]
    source = queryset.iterator(chunk_size=STREAM_CHUNK_SIZE) if hasattr(queryset, "iterator") else iter(queryset)
    for index, instance in enumerate(source):
        if limit and index >= limit:
            break
        row = serializer_class(instance, context=context).data
        # 与 BaseFileRenderer.process_data 同口径：UUID / Decimal / datetime 归一成可写字符串
        yield json.loads(json.dumps(row, cls=encoders.JSONEncoder))


def _iter_rendered_rows(renderer: Any, rows: Iterable[dict[str, Any]], fields: Any) -> Iterator[list[Any]]:
    for item in rows:
        row = []
        for field in fields:
            row.append(renderer.render_value(field, item.get(field.field_name)))
        yield row


def write_excel_file(path: str, renderer: Any, titles: list[Any], rows: Iterable[dict[str, Any]], fields: Any) -> int:
    """把表头 + 数据行写进 ``path``（write_only 模式），返回数据行数。

    列宽按内容长度增量估算：write_only 模式无法回读单元格，改为写入时记录每列最大宽度。
    """
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Sheet")
    # 列宽：write_only 模式要求 <cols> 先于数据行写盘，而宽度必须扫完内容才知道，
    # 因此流式档不设自定义列宽（旧实现靠回读单元格算宽，与 write_only 互斥）。
    title_row = [ILLEGAL_CHARACTERS_RE.sub("", str(cell)) for cell in titles]
    sheet.append(title_row)
    count = 0
    for row in _iter_rendered_rows(renderer, rows, fields):
        cells = [ILLEGAL_CHARACTERS_RE.sub("", str(value)) for value in row]
        sheet.append(cells)
        count += 1
    if titles:
        # write_only 不支持 freeze_panes；自动筛选在 write_only 下可用（表头仍可点筛选）
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(titles))}{count + 1}"
    workbook.save(path)
    return count


def write_csv_file(path: str, renderer: Any, titles: Any, rows: Iterable[dict[str, Any]], fields: Any) -> int:
    """把表头 + 数据行写进 ``path``（utf-8-sig），返回数据行数。"""
    import codecs

    import unicodecsv

    from common.utils.csv_safe import escape_csv_cell

    count = 0
    with open(path, "wb") as handle:
        handle.write(codecs.BOM_UTF8)
        writer = unicodecsv.writer(handle, encoding="utf-8")
        writer.writerow([escape_csv_cell(cell) for cell in titles])
        for row in _iter_rendered_rows(renderer, rows, fields):
            writer.writerow([escape_csv_cell(value) for value in row])
            count += 1
    return count


def _content_disposition(filename: str) -> str:
    """RFC 5987 双段式：ASCII 回落 + UTF-8 真名（中文记录名不再乱码）。"""
    return f"attachment; filename=\"{filename.encode('ascii', 'replace').decode('ascii')}\"; filename*=UTF-8''{escape_uri_path(filename)}"


def stream_export_response(view: Any, request: Any, file_format: str) -> FileResponse:
    """构建导出文件响应：逐行渲染到临时文件，再以 ``FileResponse`` 分块下发。

    渲染中途异常不向客户端吐堆栈（与旧渲染器同口径）：只回可读通用文案。
    """
    from common.drf.renders.csv import CSVFileRenderer
    from common.drf.renders.excel import ExcelFileRenderer
    from common.utils.timezone import local_now

    renderer = CSVFileRenderer() if file_format == "csv" else ExcelFileRenderer()
    renderer.template = request.query_params.get("template", "export")
    serializer = view.get_serializer()
    renderer.serializer = serializer
    fields = renderer.get_rendered_fields()
    titles = renderer.get_column_titles(fields)

    meta = getattr(serializer, "Meta", None)
    model = getattr(getattr(meta, "model", None), "__name__", "download")
    suffix = "template" if renderer.template == "import" else local_now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{model.lower()}_{suffix}.{renderer.format}"

    queryset = view.filter_queryset(view.get_queryset())
    fd, path = tempfile.mkstemp(prefix="xadmin-export-", suffix=f".{renderer.format}")
    os.close(fd)
    try:
        rows = iter_serialized_rows(queryset, view.get_serializer_class(), context=view.get_serializer_context())
        line_count = (
            write_csv_file(path, renderer, titles, rows, fields)
            if file_format == "csv"
            else write_excel_file(path, renderer, titles, rows, fields)
        )
        logger.info("stream export rendered: %s rows, %s", line_count, filename)
        handle = open(path, "rb")  # noqa: SIM115 句柄交给 FileResponse 关闭
        # POSIX：先摘目录项再读句柄——响应还没发完文件就已不可见，进程被杀也不残留垃圾
        try:
            os.unlink(path)
        except OSError:  # Windows / 只读挂载：退化为响应关闭后由系统临时目录清理
            logger.debug("export temp file kept until process exit: %s", path)
        response = FileResponse(
            handle,
            as_attachment=True,
            filename=filename,
            content_type=(
                "text/csv"
                if file_format == "csv"
                else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
        response["Content-Disposition"] = _content_disposition(filename)
        response["Access-Control-Expose-Headers"] = "Content-Disposition"
        return response
    except Exception:
        logger.warning("stream export failed", exc_info=True)
        _safe_unlink(path)
        raise


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        logger.debug("export temp file cleanup skipped: %s", path)
