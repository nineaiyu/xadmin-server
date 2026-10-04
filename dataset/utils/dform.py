#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动态表单校验：schema 与提交数据共用一套规则（写入/提交双侧）。

安全边界：控件类型收敛 14 种；key 格式固定且表单内唯一；字段数 ≤50；
提交数据未知键/required 缺失/选项外取值/数值越界/超长一律拒绝。
upload 仅接受文件条目（pk 等字段，不落文件本体）；给出提交人时断言文件存在且归属该用户；
table 禁嵌套、限行列数；
user 仅接受用户主键（不校验存在性——纯函数无 DB，落库后由业务读取时兜底）；
cascader 仅接受命中选项树叶子路径的值序列；
select/radio/checkbox 的选项可以内联（options）或绑定数据字典（dict）——
绑定字典时选项值集合在**提交校验时**从字典读取（带缓存），schema 侧只校验
字典 code 格式且与内联 options 互斥（避免两处定义漂移）。
formula（计算字段）只读：schema 声明公式表达式（`formula` 属性，语法与语义
见 dataset/utils/dform_formula.py，前端镜像同口径），提交时由服务端按当前数据
重算并覆盖（不接受客户端提交值），不可设为必填；
草稿（DRAFT）走 `validate_draft_data` 轻校验：只做结构/体积收敛，
必填与取值在「提交」时按完整规则统一校验。

联动规则（``schema.linkages``，可空）：每条 ``{target, field, op, value, effect}``
声明「触发字段满足条件时对目标字段的效果」（隐藏/显示/必填/非必填）；
服务端在提交校验时按同一口径求值（前端镜像同一份规则做展示层联动）：
- **隐藏**的目标字段跳过必填与取值校验，且不写入落库数据（隐藏即不生效）；
- **必填/非必填**在字段自身 required 之上覆盖；
- 同一目标多条规则命中时，按数组顺序**后者覆盖前者**（分效果维度独立）。
"""

import json
from typing import Any

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from dataset.utils.dform_constants import (  # noqa: F401 再导出：常量事实源见该模块
    ALLOWED_TYPES,
    DATE_RE,
    DICT_CODE_RE,
    FILTERABLE_TYPES,
    KEY_RE,
    LINKAGE_EFFECTS,
    LINKAGE_OPS,
    MAX_CASCADER_DEPTH,
    MAX_CASCADER_NODES,
    MAX_DRAFT_BYTES,
    MAX_FIELDS,
    MAX_LINKAGES,
    MAX_OPTIONS,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
    MAX_TEXT_LENGTH,
    MAX_UPLOAD_FILES,
    MAX_USER_PICKS,
    OPTIONED_TYPES,
    TABLE_COLUMN_TYPES,
    TEXTUAL_TYPES,
    VALUED_LINKAGE_OPS,
)
from dataset.utils.dform_fields import (  # noqa: F401 再导出：字段值工具调用面保持不变
    _cascader_path_valid,
    _validate_cascader_options,
    _validate_user_pk,
    assert_upload_ownership,
    field_option_values,
    normalize_table_row,
)
from dataset.utils.dform_formula import evaluate_formula_fields, validate_formula_fields
from dataset.utils.dform_linkage import evaluate_linkages, validate_linkages

logger = get_logger(__name__)


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
        if ftype in ("amount", "formula"):
            precision = item.get("precision")
            if precision is not None and (
                not isinstance(precision, int) or isinstance(precision, bool) or not 0 <= precision <= 6
            ):
                raise ValidationError(_("Field {} precision must be 0-6").format(key))
        if ftype == "formula":
            expression = item.get("formula")
            if not isinstance(expression, str) or not expression.strip():
                raise ValidationError(_("Field {} requires a formula expression").format(key))
            if item.get("required"):
                raise ValidationError(_("Field {} is a computed field and cannot be required").format(key))
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
        filterable = item.get("filterable")
        if filterable is not None and not isinstance(filterable, bool):
            raise ValidationError(_("Field {} filterable must be boolean").format(key))
        if filterable and ftype not in FILTERABLE_TYPES:
            raise ValidationError(_("Field {} of type {} cannot be filterable").format(key, ftype))
    # 公式字段的引用合法性与循环引用需在全部字段可见后统一校验
    validate_formula_fields(fields)
    return fields


def normalize_schema(schema: dict) -> dict:
    """校验 + 规范化 schema（写入侧唯一入口）：字段顺序即渲染顺序。

    顶层未声明键丢弃；联动规则缺省不写入（保持存量 schema 形态零变化）。
    字段级属性维持既有「只校验不改写」口径（历史数据兼容优先）。
    """
    fields = validate_schema(schema)
    normalized = {"fields": fields}
    linkages = validate_linkages(schema, fields)
    if linkages:
        normalized["linkages"] = linkages
    return normalized


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


def trim_stale_schema_keys(schema: dict, data):
    """按当前 schema 裁剪 data 中的历史键（仅用于**存储数据回填**路径）。

    场景：表单 schema 演进（字段删除/改名）后，旧提交/草稿的 data 含已删除字段
    的键——渲染端不展示、用户无法清理，直接按当前 schema 严格校验必被
    「Unknown submission keys」拒绝（编辑重提 / 草稿提交 / 驳回重提全部卡死）。
    存储数据的回填路径先经此处裁剪再校验；客户端请求**显式提交**的数据不走
    本函数，未知键仍由 validate_submission_data 严格拒绝（写入侧口径不变）。
    """
    fields = schema.get("fields") if isinstance(schema, dict) else None
    if not isinstance(fields, list) or not isinstance(data, dict):
        return data
    known = {item.get("key") for item in fields if isinstance(item, dict)}
    stale = [key for key in data if key not in known]
    if stale:
        logger.warning("drop stale submission keys after schema change: %s", ", ".join(sorted(map(str, stale))))
        return {key: value for key, value in data.items() if key in known}
    return data


def validate_submission_data(schema: dict, data, user=None) -> dict:
    """提交数据校验：未知键拒绝 + required + 类型/选项/边界校验。返回规范化 data。

    联动优先：先按原始数据求值联动规则——被隐藏的字段跳过全部校验且不写入
    （隐藏即不生效），动态必填/非必填覆盖字段自身定义（见 ``evaluate_linkages``）。

    ``user`` 给出时，upload 控件值额外做归属断言（文件必须存在且由该用户上传）；
    省略（种子/脚本/无请求上下文）则跳过该断言，其余校验不变。
    """
    fields = schema.get("fields") if isinstance(schema, dict) else None
    if not isinstance(fields, list):
        raise ValidationError(_("Invalid form schema"))
    if not isinstance(data, dict):
        raise ValidationError(_("Invalid submission data"))

    known = {item["key"]: item for item in fields}
    unknown = set(data) - set(known)
    if unknown:
        raise ValidationError(_("Unknown submission keys: {}").format(", ".join(sorted(unknown))))

    controls = evaluate_linkages(schema, data)
    normalized: dict[str, Any] = {}
    for item in fields:
        key = item["key"]
        ftype = item["type"]
        label = item.get("label") or key
        value = data.get(key)
        control = controls.get(key) or {}
        if control.get("hidden"):
            normalized[key] = None
            continue
        if ftype == "formula":
            # 计算字段：不接受客户端提交值，统一在循环后按依赖顺序求值覆盖
            # （隐藏时保持 None，由求值阶段识别 hidden 集合）
            normalized[key] = None
            continue
        required = bool(item.get("required"))
        if control.get("required") is not None:
            required = bool(control["required"])
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
        elif ftype == "date":
            # 日期值契约 YYYY-MM-DD（与明细子表 date 列同口径）：数据集趋势按 ISO 前缀截断分桶，
            # 非 ISO 值会让桶失真
            if not isinstance(value, str) or not DATE_RE.match(value):
                raise ValidationError(_("Field {} must be a date (YYYY-MM-DD)").format(label))
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
            if user is not None and getattr(user, "pk", None):
                assert_upload_ownership(value, label, user)
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
    if any(item["type"] == "formula" for item in fields):
        hidden_keys = {key for key, control in controls.items() if control.get("hidden")}
        normalized.update(evaluate_formula_fields(fields, normalized, hidden_keys))
    return normalized
