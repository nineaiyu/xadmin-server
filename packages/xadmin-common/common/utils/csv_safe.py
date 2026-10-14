#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""CSV 输出公式注入防护。

Excel / WPS / Numbers 打开 CSV 时会把 ``=`` / ``+`` / ``-`` / ``@`` 开头的内容
按公式求值（外链 / DDE / 宏），``0`` 前缀用于保留编号、电话类文本形态。防护手法
= 前缀 ``'``（单引号）；导入侧 ``CSVFileParser.match_escape_chars`` 按同一前缀
清单剥离 ``'``，保证「导出 → 填写 → 导入」往返一致。

数值型负数（``-100`` / ``-1,234.5``）豁免：Excel 视其为数字而非公式，一并转义
会把度量 / 财务类数据变成带引号的文本单元格，损失可计算性。
"""

import re
from typing import Any

from ..drf.const import CSV_FILE_ESCAPE_CHARS

#: 数值型负数（可选千分位与小数）——不构成公式 payload，保持原样
_NUMERIC_NEGATIVE = re.compile(r"^-\s*[\d,]*\.?\d+$")


def escape_csv_cell(value: Any) -> Any:
    """危险前缀的字符串单元格加 ``'`` 前缀；非字符串与数值型负数原样返回。"""
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped.startswith(tuple(CSV_FILE_ESCAPE_CHARS)) and not _NUMERIC_NEGATIVE.match(stripped):
        return f"'{value}"
    return value
