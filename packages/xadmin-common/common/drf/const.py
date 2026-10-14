"""CSV 公式注入防护的前缀清单（导出加引号 / 导入剥离引号共用）。

``=`` / ``@`` / ``+`` / ``-`` 是 Excel 一族的公式触发前缀；``0`` 用于保留
编号、电话类文本形态（避免 Excel 吞掉前导零）。导出侧 ``escape_csv_cell``
对数值型负数豁免（见 common/utils/csv_safe.py），导入侧
``CSVFileParser.match_escape_chars`` 按本清单剥离 ``'`` 前缀。
"""

CSV_FILE_ESCAPE_CHARS = ["=", "@", "0", "+", "-"]
