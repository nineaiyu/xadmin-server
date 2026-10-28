# -*- coding: utf-8 -*-
"""导出流式化（PERF-EXP-01）：逐行渲染、落盘、文件响应。

守护点（旧实现是「全量列表 + json 往返副本 + 整本工作簿进内存」）：

1. 取数走 ``queryset.iterator()``，且**懒执行**——只取第一行时不消费整个游标；
2. 逐行序列化复用列表接口的 serializer（单元格取值与旧渲染器一致）；
3. Excel 用 write_only 模式；CSV 带 BOM；两者都能被解析回原样；
4. ``stream_export_response`` 返回 ``FileResponse``，临时文件在打开后即摘除目录项
   （进程被杀也不留垃圾），且 Content-Disposition 带 UTF-8 文件名。
"""

from __future__ import annotations

import io
import os
from types import SimpleNamespace

from django.http import FileResponse
from openpyxl import load_workbook
from rest_framework import serializers

from common.core.modelset.import_export.streaming import (
    iter_serialized_rows,
    stream_export_response,
    write_csv_file,
    write_excel_file,
)


class _RowSerializer(serializers.Serializer):
    name = serializers.CharField()
    amount = serializers.IntegerField()


class _LazyQueryset:
    """模拟 QuerySet：iterator() 按需产出，并记录被消费的行数。"""

    def __init__(self, rows):
        self.rows = rows
        self.consumed = 0
        self.iterator_kwargs = None

    def iterator(self, **kwargs):
        self.iterator_kwargs = kwargs
        for row in self.rows:
            self.consumed += 1
            yield row


class _FakeRenderer:
    """只借 render_value 的字段语义（与真实渲染器同一实现）。"""

    def render_value(self, field, value):
        if value is None:
            return "-"
        return str(value)


def _fields():
    return [
        SimpleNamespace(field_name="name", label="名称"),
        SimpleNamespace(field_name="amount", label="数量"),
    ]


class TestIterSerializedRows:
    def test_lazy_iteration_and_limit(self):
        source = _LazyQueryset([{"name": f"n{i}", "amount": i} for i in range(10)])

        stream = iter_serialized_rows(source, _RowSerializer, limit=3)
        first = next(stream)

        assert first == {"name": "n0", "amount": 0}
        # 懒执行：只取了 1 行，没有把 10 行全部消费掉
        assert source.consumed == 1
        assert source.iterator_kwargs and source.iterator_kwargs["chunk_size"] > 0

        rest = list(stream)
        assert [row["name"] for row in rest] == ["n1", "n2"]
        assert source.consumed <= 4  # 至多多拉一行用于判断越界（无切片能力的源）

    def test_plain_list_fallback_keeps_limit(self):
        rows = [{"name": "a", "amount": 1}, {"name": "b", "amount": 2}]
        assert len(list(iter_serialized_rows(rows, _RowSerializer, limit=1))) == 1


class TestWriteExcelFile:
    def test_write_only_workbook_roundtrip(self, tmp_path):
        path = str(tmp_path / "out.xlsx")
        rows = [{"name": "甲", "amount": 1}, {"name": "乙", "amount": 2}]

        count = write_excel_file(path, _FakeRenderer(), ["名称(name)", "数量(amount)"], rows, _fields())

        assert count == 2
        sheet = load_workbook(path).active
        assert [cell.value for cell in sheet[1]] == ["名称(name)", "数量(amount)"]
        assert [cell.value for cell in sheet[2]] == ["甲", "1"]
        # write_only 不支持 freeze_panes，但自动筛选仍覆盖表头到末行
        assert sheet.auto_filter.ref == "A1:B3"
        # 流式档不设自定义列宽（write_only 下 <cols> 必须先于数据行，见实现注释）


class TestWriteCsvFile:
    def test_bom_and_rows(self, tmp_path):
        path = str(tmp_path / "out.csv")
        count = write_csv_file(
            path, _FakeRenderer(), ["名称(name)", "数量(amount)"], [{"name": "=危险", "amount": 3}], _fields()
        )

        assert count == 1
        raw = open(path, "rb").read()
        assert raw.startswith(b"\xef\xbb\xbf")  # BOM
        text = raw.decode("utf-8-sig")
        assert "名称(name)" in text
        # CSV 注入防护：= 开头被转义（escape_csv_cell 的既有口径）
        assert "'=危险" in text or "=危险" not in text.splitlines()[1]


class TestStreamExportResponse:
    def _view(self, rows):
        source = _LazyQueryset(rows)

        class _View:
            def get_queryset(self):
                return source

            def filter_queryset(self, queryset):
                return queryset

            def get_serializer_class(self):
                return _RowSerializer

            def get_serializer_context(self):
                return {}

            def get_serializer(self):
                return _RowSerializer()

        return _View()

    def _request(self, fmt):
        return SimpleNamespace(query_params={"type": fmt})

    def test_excel_response_streams_and_cleans_temp_file(self, tmp_path):
        view = self._view([{"name": "甲", "amount": 1}])
        response = stream_export_response(view, self._request("xlsx"), "xlsx")

        assert isinstance(response, FileResponse)
        assert "spreadsheetml" in response["Content-Type"]
        assert "attachment;" in response["Content-Disposition"]
        assert "filename*=UTF-8''" in response["Content-Disposition"]

        body = b"".join(response.streaming_content)
        assert body[:2] == b"PK"  # xlsx 是 zip 容器
        # 打开后即摘除目录项：句柄仍可读，但路径已不存在
        assert not os.path.exists(response.file_to_stream.name) or not os.path.exists(
            getattr(response, "_xadmin_temp_path", response.file_to_stream.name)
        )

    def test_csv_response_content(self):
        view = self._view([{"name": "甲", "amount": 1}, {"name": "乙", "amount": 2}])
        response = stream_export_response(view, self._request("csv"), "csv")

        text = b"".join(response.streaming_content).decode("utf-8-sig")
        lines = [line for line in text.strip().splitlines() if line]
        assert lines[0].startswith("*Name(name)")  # required 字段带 * 前缀（框架既有口径）
        assert "甲" in lines[1] and "乙" in lines[2]


def test_iter_uses_serializer_representation():
    """单元格取值来自 serializer（与列表接口同源），不是模型裸字段。"""

    class _Upper(serializers.Serializer):
        name = serializers.CharField(source="raw_name")

    row = next(iter(iter_serialized_rows([{"raw_name": "x"}], _Upper, limit=1)))
    assert row == {"name": "x"}


def test_temp_file_is_unlinked_immediately(tmp_path, monkeypatch):
    """临时文件先摘目录项再交出句柄：响应没发完时进程被杀也不留垃圾。"""
    created = {}

    def _fake_mkstemp(prefix="", suffix=""):
        path = str(tmp_path / ("tmp" + suffix))
        fd = os.open(path, os.O_CREAT | os.O_RDWR)
        created["path"] = path
        return fd, path

    monkeypatch.setattr("common.core.modelset.import_export.streaming.tempfile.mkstemp", _fake_mkstemp)

    class _View:
        def get_queryset(self):
            return [{"name": "a", "amount": 1}]

        def filter_queryset(self, queryset):
            return queryset

        def get_serializer_class(self):
            return _RowSerializer

        def get_serializer_context(self):
            return {}

        def get_serializer(self):
            return _RowSerializer()

    response = stream_export_response(_View(), SimpleNamespace(query_params={}), "csv")
    assert not os.path.exists(created["path"])
    assert io.DEFAULT_BUFFER_SIZE > 0  # 句柄仍可读（下面读取内容）
    assert b"a" in b"".join(response.streaming_content)
