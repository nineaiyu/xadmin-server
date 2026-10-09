# ~*~ coding: utf-8 ~*~
#
from collections.abc import Iterator
from functools import cached_property
from typing import Any

import chardet
import unicodecsv

from ..const import CSV_FILE_ESCAPE_CHARS
from .base import BaseFileParser


class CSVFileParser(BaseFileParser):
    media_type = "text/csv"

    @cached_property
    def match_escape_chars(self) -> Any:
        chars = []
        for c in CSV_FILE_ESCAPE_CHARS:
            dq_char = f'"{c}'
            sg_char = f"'{c}"
            chars.append(dq_char)
            chars.append(sg_char)
        return tuple(chars)

    @staticmethod
    def _universal_newlines(stream: Any) -> Iterator[Any]:
        """
        保证在`通用换行模式`下打开文件
        """
        yield from stream.splitlines()

    def __parse_row(self, row: Any) -> Any:
        row_escape = []
        for d in row:
            if isinstance(d, str) and d.strip().startswith(self.match_escape_chars):
                d = d.lstrip("'").lstrip('"')
            row_escape.append(d)
        return row_escape

    def generate_rows(self, stream_data: Any) -> Iterator[list[Any]]:
        detect_result = chardet.detect(stream_data)
        encoding = detect_result.get("encoding", "utf-8")
        lines = self._universal_newlines(stream_data)
        csv_reader = unicodecsv.reader(lines, encoding=encoding)
        for row in csv_reader:
            row = self.__parse_row(row)
            yield row
