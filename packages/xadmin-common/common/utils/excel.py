#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""openpyxl 落盘辅助：公式注入防护。

openpyxl 给单元格赋值时会把「``=`` 开头的字符串」自动判定为公式
（``Cell._bind_value`` → ``data_type = "f"``），保存后 Excel / WPS 打开即按
公式求值。业务数据（用户名、备注、数据集字段值）来自用户输入，直接 ``append``
写盘会形成公式注入通道（外链 / DDE）。本模块提供统一落行入口：写入后把
**字符串**单元格显式标记为文本（``data_type = "s"``），数值 / 时间单元格保持
原类型——防注入的同时不影响数字列的可计算性与单元格格式。
"""

from typing import Any


def append_text_safe_row(worksheet: Any, row: Any) -> None:
    """向工作表追加一行，并把字符串单元格强制为文本类型（防公式注入）。

    仅覆盖字符串值：openpyxl 对 ``=`` 开头的字符串会置 ``data_type="f"``，
    显式改回 ``"s"`` 后 Excel 按纯文本展示、不再求值；数值 / 日期 / 布尔
    单元格不受影响。
    """
    worksheet.append(row)
    for cell in worksheet[worksheet.max_row]:
        if isinstance(cell.value, str):
            cell.data_type = "s"
