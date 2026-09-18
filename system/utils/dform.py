#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动态表单校验：schema 与提交数据共用一套规则（写入/提交双侧）。

安全边界：控件类型收敛 14 种；key 格式固定且表单内唯一；字段数 ≤50；
提交数据未知键/required 缺失/选项外取值/数值越界/超长一律拒绝。
upload 仅接受文件 pk 字符串数组（不落文件本体）；table 禁嵌套、限行列数；
user 仅接受用户主键（不校验存在性——纯函数无 DB，落库后由业务读取时兜底）；
cascader 仅接受命中选项树叶子路径的值序列；
select/radio/checkbox 的选项可以内联（options）或绑定数据字典（dict）——
绑定字典时选项值集合在**提交校验时**从字典读取（带缓存），schema 侧只校验
字典 code 格式且与内联 options 互斥（避免两处定义漂移）。
草稿（DRAFT）走 `validate_draft_data` 轻校验：只做结构/体积收敛，
必填与取值在「提交」时按完整规则统一校验。
"""

import json
import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

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


def _validate_cascader_options(key: str, options):
    """级联选项树校验：{value,label[,children]} 递归 ≤3 层、节点总数封顶。"""
    if not isinstance(options, list) or not options:
        raise ValidationError(_("Field {} requires options").format(key))
    counter = {"total": 0}

    def walk(nodes, depth):
        if depth > MAX_CASCADER_DEPTH:
            raise ValidationError(_("Field {} cascader options exceed {} levels").format(key, MAX_CASCADER_DEPTH))
        for node in nodes:
            if not isinstance(node, dict):
                raise ValidationError(_("Field {} has an invalid cascader option").format(key))
            value = node.get("value")
            # 值允许字符串或整数（布尔是 int 子类，显式排除）
            if isinstance(value, bool) or not isinstance(value, (str, int)) or str(value).strip() == "":
                raise ValidationError(_("Field {} cascader option requires a value").format(key))
            if not str(node.get("label") or "").strip():
                raise ValidationError(_("Field {} cascader option requires a label").format(key))
            counter["total"] += 1
            if counter["total"] > MAX_CASCADER_NODES:
                raise ValidationError(_("Field {} has too many cascader options").format(key))
            children = node.get("children")
            if children is not None:
                if not isinstance(children, list) or not children:
                    raise ValidationError(_("Field {} has an invalid cascader option").format(key))
                walk(children, depth + 1)

    walk(options, 1)


def _validate_user_pk(label: str, value):
    """选人控件取值：正整数用户主键（不接受布尔/字符串/浮点）。"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(_("Field {} must be a user id").format(label))


def _cascader_path_valid(options, path: list) -> bool:
    """级联取值必须命中选项树的叶子路径（逐段比对，值类型允许 str/int）。"""
    nodes = options
    for index, step in enumerate(path):
        node = next((item for item in nodes if item.get("value") == step), None)
        if node is None:
            return False
        last = index == len(path) - 1
        children = node.get("children")
        if last:
            return not children
        if not children:
            return False
        nodes = children
    return False


def validate_schema(schema: dict) -> list:
    """校验表单 schema，返回规范化字段列表。"""
    if not isinstance(schema, dict):
        raise ValidationError(_("Invalid form schema"))
    fields = schema.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValidationError(_("Form schema must contain fields"))
    if len(fields) > MAX_FIELDS:
        raise ValidationError(_("Form fields exceed the limit of {}").format(MAX_FIELDS))

    seen = set()
    for item in fields:
        if not isinstance(item, dict):
            raise ValidationError(_("Invalid form schema"))
        key = str(item.get("key") or "")
        if not KEY_RE.match(key):
            raise ValidationError(_("Field key {} is invalid (lowercase letters/digits/underscore)").format(key))
        if key in seen:
            raise ValidationError(_("Duplicate field key: {}").format(key))
        seen.add(key)
        if not str(item.get("label") or "").strip():
            raise ValidationError(_("Field {} requires a label").format(key))
        ftype = item.get("type")
        if ftype not in ALLOWED_TYPES:
            raise ValidationError(_("Unknown widget type: {}").format(ftype))
        options = item.get("options")
        dict_code = item.get("dict")
        if ftype in OPTIONED_TYPES:
            if dict_code is not None and dict_code != "":
                # 字典驱动：code 格式合法且与内联 options 互斥（选项值集合在提交校验时读字典）
                if not isinstance(dict_code, str) or not DICT_CODE_RE.match(dict_code.strip()):
                    raise ValidationError(_("Field {} has an invalid dict code").format(key))
                if options not in (None, []):
                    raise ValidationError(_("Field {} cannot use both options and dict").format(key))
            else:
                if not isinstance(options, list) or not options:
                    raise ValidationError(_("Field {} requires options").format(key))
                if len(options) > MAX_OPTIONS:
                    raise ValidationError(_("Field {} has too many options").format(key))
        elif dict_code not in (None, ""):
            raise ValidationError(_("Field {} of type {} does not accept a dict").format(key, ftype))
        elif ftype == "cascader":
            _validate_cascader_options(key, options)
        elif options not in (None, []):
            raise ValidationError(_("Field {} of type {} does not accept options").format(key, ftype))
        if ftype == "user" and item.get("multiple") is not None and not isinstance(item.get("multiple"), bool):
            raise ValidationError(_("Field {} multiple must be boolean").format(key))
        if ftype == "amount":
            precision = item.get("precision")
            if precision is not None and (
                not isinstance(precision, int) or isinstance(precision, bool) or not 0 <= precision <= 6
            ):
                raise ValidationError(_("Field {} precision must be 0-6").format(key))
        if ftype == "table":
            columns = item.get("columns")
            if not isinstance(columns, list) or not columns:
                raise ValidationError(_("Field {} requires table columns").format(key))
            if len(columns) > MAX_TABLE_COLUMNS:
                raise ValidationError(_("Field {} has too many table columns").format(key))
            seen_columns = set()
            for column in columns:
                if not isinstance(column, dict):
                    raise ValidationError(_("Field {} has an invalid table column").format(key))
                column_key = str(column.get("key") or "")
                if not KEY_RE.match(column_key):
                    raise ValidationError(_("Table column key {} is invalid").format(column_key))
                if column_key in seen_columns:
                    raise ValidationError(_("Duplicate table column key: {}").format(column_key))
                seen_columns.add(column_key)
                if not str(column.get("label") or "").strip():
                    raise ValidationError(_("Table column {} requires a label").format(column_key))
                column_type = column.get("type")
                if column_type not in TABLE_COLUMN_TYPES:
                    raise ValidationError(_("Unsupported table column type: {}").format(column_type))
                column_options = column.get("options")
                if column_type == "select":
                    if not isinstance(column_options, list) or not column_options:
                        raise ValidationError(_("Table column {} requires options").format(column_key))
                elif column_options not in (None, []):
                    raise ValidationError(_("Table column {} does not accept options").format(column_key))
        max_length = item.get("max_length")
        if max_length is not None and (not isinstance(max_length, int) or not (1 <= max_length <= MAX_TEXT_LENGTH)):
            raise ValidationError(_("Field {} max_length must be 1-{}").format(key, MAX_TEXT_LENGTH))
        for bound in ("min", "max"):
            value = item.get(bound)
            if value is not None and not isinstance(value, (int, float)):
                raise ValidationError(_("Field {} {} must be numeric").format(key, bound))
    return fields


def normalize_table_row(item: dict, label: str, row) -> dict:
    """明细子表单行校验：按列定义逐列校验，未知列键拒绝，返回规范化行。"""
    if not isinstance(row, dict):
        raise ValidationError(_("Field {} rows must be objects").format(label))
    columns = item.get("columns") or []
    known = {column["key"]: column for column in columns}
    unknown = set(row) - set(known)
    if unknown:
        raise ValidationError(_("Field {} has unknown columns: {}").format(label, ", ".join(sorted(unknown))))
    normalized = {}
    for column in columns:
        column_key = column["key"]
        column_type = column.get("type")
        value = row.get(column_key)
        if value is None or value == "":
            normalized[column_key] = None
            continue
        if column_type == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValidationError(_("Field {} must be numeric").format(column_key))
        elif column_type == "select":
            if value not in (column.get("options") or []):
                raise ValidationError(_("Field {} has an invalid option: {}").format(column_key, value))
        elif column_type == "date":
            if not isinstance(value, str) or not DATE_RE.match(value):
                raise ValidationError(_("Field {} must be a date").format(column_key))
        else:
            if not isinstance(value, str):
                raise ValidationError(_("Field {} must be text").format(column_key))
            if len(value) > MAX_TEXT_LENGTH:
                raise ValidationError(_("Field {} exceeds the max length {}").format(column_key, MAX_TEXT_LENGTH))
        normalized[column_key] = value
    return normalized


def field_option_values(item: dict) -> list:
    """选项型字段的合法取值集合：绑定字典时读字典项 value（5 分钟缓存，失败降级空集）。

    空集合语义 = fail-closed：字典被清空/停用时该字段不接受任何取值（提交被拒）。
    """
    dict_code = item.get("dict")
    if dict_code:
        from system.utils.dict import get_dict_items

        return [row.get("value") for row in get_dict_items(str(dict_code)) if row.get("value") is not None]
    return list(item.get("options") or [])


def validate_draft_data(data) -> dict:
    """草稿轻校验：数据须为对象、键为合法字段 key 形态、体积封顶；不做必填/取值校验。

    草稿的完整校验在「提交」时统一执行（`validate_submission_data`），
    避免用户暂存未填完的表单被后端拒绝。
    """
    if not isinstance(data, dict):
        raise ValidationError(_("Invalid submission data"))
    for key in data:
        if not isinstance(key, str) or not KEY_RE.match(key):
            raise ValidationError(_("Field key {} is invalid (lowercase letters/digits/underscore)").format(key))
    try:
        size = len(json.dumps(data, ensure_ascii=False, default=str))
    except (TypeError, ValueError) as exc:
        raise ValidationError(_("Invalid submission data")) from exc
    if size > MAX_DRAFT_BYTES:
        raise ValidationError(_("Draft data exceeds the size limit"))
    return data


def validate_submission_data(schema: dict, data) -> dict:
    """提交数据校验：未知键拒绝 + required + 类型/选项/边界校验。返回规范化 data。"""
    fields = schema.get("fields") if isinstance(schema, dict) else None
    if not isinstance(fields, list):
        raise ValidationError(_("Invalid form schema"))
    if not isinstance(data, dict):
        raise ValidationError(_("Invalid submission data"))

    known = {item["key"]: item for item in fields}
    unknown = set(data) - set(known)
    if unknown:
        raise ValidationError(_("Unknown submission keys: {}").format(", ".join(sorted(unknown))))

    normalized = {}
    for item in fields:
        key = item["key"]
        ftype = item["type"]
        label = item.get("label") or key
        value = data.get(key)
        required = bool(item.get("required"))
        if value is None or value == "" or value == []:
            if required:
                raise ValidationError(_("Field {} is required").format(label))
            normalized[key] = None
            continue
        if ftype in ("number", "amount"):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValidationError(_("Field {} must be numeric").format(label))
            if item.get("min") is not None and value < item["min"]:
                raise ValidationError(_("Field {} is below the minimum").format(label))
            if item.get("max") is not None and value > item["max"]:
                raise ValidationError(_("Field {} is above the maximum").format(label))
            precision = item.get("precision")
            if ftype == "amount" and precision is not None and abs(value - round(value, precision)) > 1e-9:
                raise ValidationError(_("Field {} allows at most {} decimal places").format(label, precision))
        elif ftype == "user":
            multiple = bool(item.get("multiple"))
            if multiple:
                if not isinstance(value, list):
                    raise ValidationError(_("Field {} must be a list").format(label))
                if len(value) > MAX_USER_PICKS:
                    raise ValidationError(_("Field {} has too many users").format(label))
                for picked in value:
                    _validate_user_pk(label, picked)
            else:
                _validate_user_pk(label, value)
        elif ftype == "cascader":
            if not isinstance(value, list) or not value:
                raise ValidationError(_("Field {} must be a cascader path").format(label))
            if not all(isinstance(step, (str, int)) and not isinstance(step, bool) for step in value):
                raise ValidationError(_("Field {} must be a cascader path").format(label))
            if not _cascader_path_valid(item.get("options") or [], value):
                raise ValidationError(_("Field {} has an invalid cascader path").format(label))
        elif ftype == "checkbox":
            if not isinstance(value, list):
                raise ValidationError(_("Field {} must be a list").format(label))
            allowed = field_option_values(item)
            outside = [v for v in value if v not in allowed]
            if outside:
                raise ValidationError(_("Field {} has invalid options: {}").format(label, outside))
        elif ftype == "switch":
            if not isinstance(value, bool):
                raise ValidationError(_("Field {} must be boolean").format(label))
        elif ftype in OPTIONED_TYPES:
            if value not in field_option_values(item):
                raise ValidationError(_("Field {} has an invalid option: {}").format(label, value))
        elif ftype == "upload":
            if not isinstance(value, list):
                raise ValidationError(_("Field {} must be a list").format(label))
            if len(value) > MAX_UPLOAD_FILES:
                raise ValidationError(_("Field {} has too many files").format(label))
            # 值为文件条目对象（与通用上传组件回传结构一致：pk/filename/filesize/filepath）
            for item in value:
                if not isinstance(item, dict) or not str(item.get("pk") or "").strip():
                    raise ValidationError(_("Field {} contains an invalid file").format(label))
        elif ftype == "daterange":
            if not isinstance(value, list) or len(value) != 2:
                raise ValidationError(_("Field {} must be a date range").format(label))
            start, end = value
            if not isinstance(start, str) or not isinstance(end, str):
                raise ValidationError(_("Field {} must be a date range").format(label))
            if not (DATE_RE.match(start) and DATE_RE.match(end)):
                raise ValidationError(_("Field {} must be a date range").format(label))
            if start > end:
                raise ValidationError(_("Field {} has an invalid date range").format(label))
        elif ftype == "table":
            if not isinstance(value, list):
                raise ValidationError(_("Field {} must be a list of rows").format(label))
            if len(value) > MAX_TABLE_ROWS:
                raise ValidationError(_("Field {} has too many rows").format(label))
            normalized[key] = [normalize_table_row(item, label, row) for row in value]
            continue
        else:
            if not isinstance(value, str):
                raise ValidationError(_("Field {} must be text").format(label))
            max_length = int(item.get("max_length") or MAX_TEXT_LENGTH)
            if len(value) > max_length:
                raise ValidationError(_("Field {} exceeds the max length {}").format(label, max_length))
        normalized[key] = value
    return normalized
