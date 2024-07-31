import pyexcel
from django.utils.translation import gettext_lazy as _

from .base import BaseFileParser


class ExcelFileParser(BaseFileParser):
    media_type = 'text/xlsx'

    def generate_rows(self, stream_data):
        try:
            workbook = pyexcel.get_book(file_type='xlsx', file_content=stream_data)
        except Exception as e:
            raise Exception(_('Invalid excel file {}').format(str(e)))
        # 默认获取第一个工作表sheet
        sheet = workbook.sheet_by_index(0)
        rows = sheet.rows()
        return rows
