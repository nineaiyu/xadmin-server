import json
import os
from tempfile import NamedTemporaryFile, mktemp
from typing import Any

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo
from rest_framework.utils import encoders

from .base import BaseFileRenderer


class ExcelFileRenderer(BaseFileRenderer):
    media_type = "application/xlsx"
    format = "xlsx"

    # 工作簿/工作表在 initial_writer() 中创建（渲染器协议保证先初始化后写入）；
    # row_count 在初始化时一并复位，渲染器实例复用时不会延续上一份文档的行号
    wb: Any = None
    ws: Any = None
    row_count = 0

    def initial_writer(self) -> None:
        self.wb = Workbook()
        self.ws = self.wb.active
        self.row_count = 0

    def write_row(self, row: Any) -> None:
        self.row_count += 1
        self.ws.row_dimensions[self.row_count].height = 20
        column_count = 0
        for cell_value in row:
            # 处理非法字符
            column_count += 1
            cell_value = ILLEGAL_CHARACTERS_RE.sub(r"", str(cell_value))
            cell = self.ws.cell(row=self.row_count, column=column_count, value=str(cell_value))
            # 设置单元格格式为纯文本, 防止执行公式
            cell.data_type = "s"

    def format_values(self, data: Any, related: bool = False) -> Any:
        result = []
        for key, value in data.items():
            if related:
                result.append(f"{value}")
            else:
                result.append(f"{value}({key})")
        return json.loads(json.dumps(result, cls=encoders.JSONEncoder, ensure_ascii=False))

    def after_render(self) -> None:
        count = 0
        for col in self.ws.columns:
            max_length = 0
            count += 1
            column = col[0].column_letter
            for cell in col:
                # 行长度不齐时列尾部是空单元格（value=None），按空串计入宽度
                text = str(cell.value or "")
                if len(text) > max_length:
                    max_length = len(text)
            adjusted_width = (max_length + 2) * 1.0
            adjusted_width = 300 if adjusted_width > 300 else adjusted_width
            adjusted_width = 30 if adjusted_width < 30 else adjusted_width
            self.ws.column_dimensions[column].width = adjusted_width

        #         self.wb.save('/tmp/test.xlsx')

        if count:
            row = get_column_letter(count)
            tab = Table(displayName="Table", ref=f"A1:{row}{self.row_count}")
            style = TableStyleInfo(
                name="TableStyleLight13",
                showFirstColumn=True,
                showLastColumn=True,
                showRowStripes=True,
                showColumnStripes=True,
            )
            tab.tableStyleInfo = style
            self.ws.add_table(tab)

    def get_rendered_value(self) -> Any:
        if os.name == "nt":
            ## 针对 windows 平台，解决 NamedTemporaryFile 方法 权限异常
            tmp_name = mktemp()
            self.wb.save(tmp_name)
            with open(tmp_name, "rb") as tmp:
                value = tmp.read()
            os.unlink(tmp_name)
            return value
        else:
            with NamedTemporaryFile() as tmp:
                self.wb.save(tmp.name)
                tmp.seek(0)
                return tmp.read()
