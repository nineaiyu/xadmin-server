# -*- coding: utf-8 -*-
"""packages/xadmin-common/common/drf/renders/excel.py：xlsx 渲染的行号与列宽边界。"""

from common.drf.renders.excel import ExcelFileRenderer


class TestExcelRowCount:
    def test_row_count_resets_on_initial_writer(self):
        """同一渲染器实例复用（测试 / 长生命周期）时行号必须复位，Table.ref 不能累计。"""
        renderer = ExcelFileRenderer()
        renderer.initial_writer()
        renderer.write_row(["a", "b"])
        renderer.write_row(["c", "d"])
        assert renderer.row_count == 2

        renderer.initial_writer()
        renderer.write_row(["e", "f"])
        assert renderer.row_count == 1


class TestAfterRenderColumnWidth:
    def test_ragged_rows_with_empty_cells_do_not_break(self):
        """行长度不齐时列尾部是空单元格（value=None），宽度计算不能抛 TypeError。"""
        renderer = ExcelFileRenderer()
        renderer.initial_writer()
        renderer.write_row(["a", "b", "c"])
        renderer.write_row(["d"])  # 该行只有一列，后续列为空单元格
        renderer.after_render()
        assert renderer.row_count == 2
