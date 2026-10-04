#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""导入失败行错误报告（xlsx）。

原表头列 + error_message 汇总列；用 openpyxl 写 xlsx（导出渲染同款依赖），
产物落 UploadFile(is_tmp=True) 由调用方挂到 ImportRecord.error_report。
"""

from django.utils.translation import gettext_lazy as _

ERROR_COLUMN_TITLE = "error_message"


def build_error_report(file_path, column_titles, failed_rows):
    """生成失败行错误报告。

    :param file_path: 报告输出路径
    :param column_titles: 源文件原表头（列表）
    :param failed_rows: [{"row": 行号(1-based 数据行), "values": dict, "error": 错误串}]
    """
    from openpyxl import Workbook

    header = [str(title) if title else "" for title in column_titles]
    if ERROR_COLUMN_TITLE not in header:
        header.append(ERROR_COLUMN_TITLE)
    error_index = header.index(ERROR_COLUMN_TITLE)

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(header)
    for item in failed_rows:
        row = ["" for _ in header]
        values = item.get("values") or {}
        for idx, title in enumerate(header[:error_index]):
            value = values.get(title, values.get(str(title), ""))
            row[idx] = str(value) if value is not None else ""
        row[error_index] = item.get("error") or _("Validation failed")
        worksheet.append(row)
    workbook.save(file_path)
    return file_path
