# -*- coding: utf-8 -*-
"""导出产物公式注入防护（CSV 前缀转义 + xlsx 文本类型）守护。

业务数据来自用户输入，导出后在 Excel / WPS 打开会被按公式求值（外链 / DDE）：
- CSV 走 ``escape_csv_cell``：危险前缀（``=`` ``@`` ``+`` ``-`` ``0``）加 ``'``，
  数值型负数豁免（不损失可计算性）；
- xlsx 走 ``append_text_safe_row``：把字符串单元格标记为文本（``data_type="s"``），
  阻止 openpyxl 把 ``=`` 开头内容存成公式（``data_type="f"``），数值 / 日期保持原类型。

导出与导入（``CSVFileParser.match_escape_chars``）共用同一前缀清单，本模块同时
锁定「导出 → 填写 → 导入」的往返一致性。
"""

from datetime import datetime

import pytest
from openpyxl import Workbook

from common.drf.const import CSV_FILE_ESCAPE_CHARS
from common.drf.parsers.csv import CSVFileParser
from common.utils.csv_safe import escape_csv_cell
from common.utils.excel import append_text_safe_row


@pytest.mark.parametrize(
    ("value", "expected_prefix"),
    [
        ("=cmd|' /C calc'!A0", "'"),
        ("+8613800138000", "'"),
        ("@SUM(A1:A9)", "'"),
        ("0755-1234", "'"),
        ("-2+3+cmd|' /C calc'!A0", "'"),
    ],
)
def test_escape_csv_cell_prefixes_formula_payloads(value, expected_prefix):
    assert escape_csv_cell(value).startswith(expected_prefix + value[:1])


@pytest.mark.parametrize("value", ["-100", "-1,234.5", "-0.5", "- 3", "123", "正常文本", ""])
def test_escape_csv_cell_keeps_plain_values(value):
    """数值型负数与普通文本保持原样（负数在 Excel 中是数字而非公式）。"""
    assert escape_csv_cell(value) == value


def test_escape_csv_cell_returns_non_string_untouched():
    assert escape_csv_cell(100) == 100
    assert escape_csv_cell(None) is None


def test_csv_export_import_roundtrip_keeps_original_value():
    """导出加 ``'`` 前缀、导入按同清单剥离：往返后与原值一致。"""
    values = ["=1+1", "+86 13800138000", "@SUM(A1)", "0755", "-2+3+cmd|'!A0", "-100"]
    escaped = [escape_csv_cell(value) for value in values]

    matcher = CSVFileParser().match_escape_chars
    assert matcher, "导入侧必须有前缀识别清单"
    for value, cell in zip(values, escaped, strict=True):
        if cell.startswith("'"):
            assert cell.startswith(matcher), f"导出前缀未被导入侧识别: {cell}"
            assert cell.lstrip("'").lstrip('"') == value
        else:
            assert cell == value  # 未转义（数值型负数）原样往返


def test_escape_prefix_list_covers_excel_formula_triggers():
    """前缀清单覆盖 Excel 一族的全部公式触发符号。"""
    assert {"=", "@", "+", "-"} <= set(CSV_FILE_ESCAPE_CHARS)


def test_append_text_safe_row_marks_string_as_text_and_keeps_numbers():
    worksheet = Workbook().active

    append_text_safe_row(worksheet, ["=cmd|' /C calc'!A0", 100, -3.5, datetime(2026, 10, 14)])

    cells = list(worksheet[1])
    assert cells[0].data_type == "s"
    assert cells[0].value == "=cmd|' /C calc'!A0"  # 文本展示，不求值
    assert cells[1].data_type == "n"
    assert cells[2].data_type == "n"
    assert cells[3].data_type == "d"


def test_xlsx_saved_cell_not_a_formula(tmp_path):
    """端到端：含公式前缀的值落盘后仍为文本单元格（openpyxl 不存为公式）。"""
    worksheet = Workbook().active
    append_text_safe_row(worksheet, ["=1+1"])
    target = tmp_path / "guard.xlsx"
    worksheet.parent.save(target)

    from openpyxl import load_workbook

    reloaded = load_workbook(target).active
    assert reloaded.cell(row=1, column=1).data_type == "s"
    assert reloaded.cell(row=1, column=1).value == "=1+1"
