#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""动态表单常量（自 dataset/utils/dform.py 平移）：字段类型白名单、长度与数量上限、联动算子。

对外的既有导入面（dataset.utils.dform）经该模块再导出保持不变。
"""

import re

ALLOWED_TYPES = (
    "input",
    "textarea",
    "number",
    "amount",
    "select",
    "radio",
    "checkbox",
    "date",
    "switch",
    "upload",
    "daterange",
    "table",
    "user",
    "cascader",
    "formula",
)
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 数据字典类型 code（与 DataDict.code 口径一致：小写字母开头，字母/数字/下划线）
DICT_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
MAX_FIELDS = 50
MAX_OPTIONS = 50
MAX_TEXT_LENGTH = 2000
# 草稿体积上限（字节）：草稿允许缺必填，但仍是用户可控 JSON，收敛写入体积
MAX_DRAFT_BYTES = 64 * 1024
TEXTUAL_TYPES = ("input", "textarea", "select", "radio", "date")
OPTIONED_TYPES = ("select", "radio", "checkbox")
# 可勾选「可筛选」的字段类型（提交时物化到筛选列；upload/table/daterange 不是等值筛选面）
FILTERABLE_TYPES = (
    "input",
    "textarea",
    "number",
    "amount",
    "select",
    "radio",
    "checkbox",
    "date",
    "switch",
    "user",
    "cascader",
    "formula",
)
# 明细子表：列类型限基础控件（禁 upload/daterange/table 嵌套），行列数封顶防超深 JSON
TABLE_COLUMN_TYPES = ("input", "textarea", "number", "date", "select")
MAX_TABLE_COLUMNS = 12
MAX_TABLE_ROWS = 100
MAX_UPLOAD_FILES = 20
# 级联选项树：节点总数与层级封顶（防超深/超大 JSON）
MAX_CASCADER_NODES = 200
MAX_CASCADER_DEPTH = 3
# 选人控件多选上限
MAX_USER_PICKS = 20
# 联动规则：操作符与效果白名单 + 规则条数上限
LINKAGE_OPS = ("eq", "ne", "in", "notin", "empty", "notempty")
LINKAGE_EFFECTS = ("hide", "show", "require", "optional")
MAX_LINKAGES = 50
# 值型操作符（必须提供 value；empty/notempty 不看 value）
VALUED_LINKAGE_OPS = ("eq", "ne", "in", "notin")
