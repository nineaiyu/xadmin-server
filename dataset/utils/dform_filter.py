#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动态表单「可筛选字段」物化与列表筛选编译（物化列 + JSON 包含查询）。

设计（对齐在线建表的务实中间态，见 ADR-067 B 线）：

- 设计器可对字段勾选 ``filterable``；提交时把**可筛选字段的规范化取值**物化到
  ``DynamicFormSubmission.filter_data``（只含勾选字段，空值不写入，非筛选字段不落）；
- 该列在 PostgreSQL 上建 GIN 索引：列表筛选编译为单条 **JSON 包含** 查询
  （``filter_data @> {...}``，jsonb_ops 可命中 GIN），避免按 ``data -> key`` 逐字段
  表达式比较（那需要每个字段一条表达式索引，动态 schema 不可行）；
- 筛选编译 fail-closed：未勾选可筛选的字段 / 取值形态非法一律可读报错，不做
  「回退扫原 data 列」的兜底（那会绕过索引承诺并放大查询成本）。

物化形态（与提交校验后的规范化取值一致）：

- 标量（input/textarea/select/radio/date/switch/number/amount/user 单选）→ 原值；
- 多值（checkbox / user 多选）→ 数组；筛选时标量查询值包成单元素数组，按
  JSON 数组包含语义（「至少命中一项」）；
- cascader → 路径数组（整条路径精确匹配）；
- 不物化：upload / table / daterange（文件、明细子表与区间不是等值筛选面）。
"""

import json
from typing import Any

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from rest_framework.exceptions import ValidationError as RestValidationError

from dataset.utils.dform import FILTERABLE_TYPES, KEY_RE

# 值形态为数组的字段（筛选值按数组包含语义编译）
ARRAY_VALUE_TYPES = ("checkbox", "cascader")


def _is_empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def filterable_items(schema) -> list:
    """schema 中勾选「可筛选」且类型可物化的字段定义（顺序即展示顺序）。"""
    fields = schema.get("fields") if isinstance(schema, dict) else None
    if not isinstance(fields, list):
        return []
    return [
        item
        for item in fields
        if isinstance(item, dict)
        and item.get("key")
        and item.get("filterable")
        and item.get("type") in FILTERABLE_TYPES
    ]


def filterable_keys(schema) -> list:
    """可筛选字段 key 清单（渲染筛选控件 / 校验筛选参数共用）。"""
    return [item["key"] for item in filterable_items(schema)]


def build_filter_data(schema, data) -> dict:
    """提交时物化：只取可筛选字段的规范化取值，空值不写入。

    入参 ``data`` 必须是**已按 schema 规范化**的提交数据（submit 校验产物），
    物化值因此与筛选查询的取值形态天然同源。
    """
    rows = data if isinstance(data, dict) else {}
    materialized: dict[str, Any] = {}
    for item in filterable_items(schema):
        value = rows.get(item["key"])
        if _is_empty(value):
            continue
        materialized[item["key"]] = value
    return materialized


def _is_multi(item: dict) -> bool:
    return bool(item.get("multiple")) or item.get("type") in ARRAY_VALUE_TYPES


def _coerce_number(key: str, value):
    if isinstance(value, bool):
        raise ValidationError(_("Filter value of {} must be numeric").format(key))
    if isinstance(value, (int, float)):
        return value
    try:
        text = str(value).strip()
        number = float(text)
    except (TypeError, ValueError) as exc:
        raise ValidationError(_("Filter value of {} must be numeric").format(key)) from exc
    return int(number) if number.is_integer() else number


def _coerce_bool(key: str, value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1"):
        return True
    if text in ("false", "0"):
        return False
    raise ValidationError(_("Filter value of {} must be a boolean").format(key))


def coerce_filter_value(item: dict, value):
    """单个筛选条件取值规范化：形态与提交校验后的物化值一致。

    多值字段（checkbox / user 多选）标量自动包成单元素列表；cascader 要求整条路径。
    空值（None/空串/空数组）视为「不筛」，直接返回 None。
    """
    if _is_empty(value):
        return None
    key = item["key"]
    ftype = item.get("type")
    if ftype in ("number", "amount", "formula"):
        coerced: Any = _coerce_number(key, value)
    elif ftype == "switch":
        coerced = _coerce_bool(key, value)
    elif ftype == "user":
        if _is_multi(item):
            picks = value if isinstance(value, list) else [value]
            coerced = [int(entry) for entry in picks if str(entry).strip()]
        else:
            coerced = int(value)
    elif ftype == "cascader":
        path = value if isinstance(value, list) else [value]
        coerced = [entry for entry in path if not _is_empty(entry)]
    elif _is_multi(item):
        picks = value if isinstance(value, list) else [value]
        coerced = [entry for entry in picks if not _is_empty(entry)]
    else:
        coerced = value if isinstance(value, str) else str(value)
    if _is_empty(coerced):
        return None
    # 数组型字段的标量查询值包成单元素数组：JSON 数组包含语义（至少命中一项）
    if _is_multi(item) and not isinstance(coerced, list):
        coerced = [coerced]
    return coerced


def build_filter_contains(schema, filters) -> dict:
    """把「字段=值」筛选条件编译为 filter_data 的 JSON 包含查询对象。

    fail-closed：条件必须是对象；字段必须在当前 schema 的可筛选面内；取值形态按
    字段类型收敛。空值条件直接跳过（等价于不筛）。
    """
    if not isinstance(filters, dict) or not filters:
        return {}
    allowed = {item["key"]: item for item in filterable_items(schema)}
    contains: dict[str, Any] = {}
    for key, value in filters.items():
        item = allowed.get(str(key))
        if item is None:
            raise ValidationError(_("Field {} is not filterable").format(key))
        coerced = coerce_filter_value(item, value)
        if coerced is not None:
            contains[item["key"]] = coerced
    return contains


def _generic_scalar(value):
    """无 schema 上下文的通用取值校验：标量原样、数组逐项标量，其余拒绝。"""
    if isinstance(value, list):
        return [entry for entry in value if not _is_empty(entry)] or None
    if isinstance(value, (str, int, float, bool)):
        return value
    raise ValidationError(_("Invalid filter conditions"))


def compile_materialized_filters(raw, schema=None) -> dict:
    """解析 ``filter_data`` 查询参数（JSON 对象）并编译为包含查询对象。

    - 给出 ``schema``（管理端已选表单）：严格按该表单**当前 schema** 的可筛选面
      校验字段与取值（未勾选「可筛选」/ 类型不可物化 → 可读报错，fail-closed）；
    - 未给出 schema（如「我的填报」不限表单）：按通用形态校验（字段 key 合法、
      值为标量或标量数组）；未物化的字段自然不命中任何行（空结果），查询仍受
      行级权限收敛，无绕过面。
    """
    if raw in (None, "", "{}"):
        return {}
    try:
        filters = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise ValidationError(_("Invalid filter conditions")) from exc
    if not isinstance(filters, dict):
        raise ValidationError(_("Invalid filter conditions"))
    if not filters:
        return {}
    if schema is not None:
        return build_filter_contains(schema, filters)
    contains: dict[str, Any] = {}
    for key, value in filters.items():
        key = str(key)
        if not KEY_RE.match(key):
            raise ValidationError(_("Invalid filter conditions"))
        coerced = _generic_scalar(value)
        if coerced is not None:
            contains[key] = coerced
    return contains


def _json_contains_supported() -> bool:
    """当前数据库是否支持 JSON 包含查询（PostgreSQL 支持并可命中 GIN；sqlite 不支持）。"""
    from django.db import connection

    return connection.vendor == "postgresql"


def apply_materialized_contains(queryset, contains):
    """把编译好的筛选条件落到查询集上。

    - PostgreSQL：单条 JSON 包含查询（``filter_data @> {...}``，GIN 索引可命中）；
    - 其它后端（sqlite 测试/开发）：退化为逐键精确比较（``filter_data -> key = value``）。
      **语义差异**：数组型字段在 sqlite 上退化为「整值相等」（无法表达数组包含）——
      生产（PostgreSQL）为完整包含语义，测试环境覆盖标量面（见 test_dform_filter）。
    """
    if not contains:
        return queryset
    if _json_contains_supported():
        return queryset.filter(filter_data__contains=contains)
    condition = Q()
    for key, value in contains.items():
        condition &= Q(**{f"filter_data__{key}": value})
    return queryset.filter(condition)


class MaterializedFilterMixin(filters.FilterSet):
    """列表按物化筛选列过滤：``?filter_data={"key": value}``（JSON 对象）。

    与 ``?form=`` 同时给出时按所选表单的当前 schema 校验「可筛选」字段（fail-closed，
    见 ``compile_materialized_filters``）；编译结果是一条 JSON 包含查询
    （``filter_data @> {...}``），PostgreSQL 命中 GIN 索引。

    基类须是 FilterSet（而非 plain mixin）：django-filter 的元类只从「有
    declared_filters 的基类」收集声明过滤器，普通混入类的声明会被静默丢弃。
    """

    filter_data = filters.CharFilter(method="filter_materialized")

    def filter_materialized(self, queryset, name, value):
        from dataset.models.dform import DynamicForm

        schema = None
        request = getattr(self, "request", None)
        # DRF Request 走 query_params；直调（Django WSGIRequest）回退 GET
        params = getattr(request, "query_params", None) or getattr(request, "GET", None)
        form_pk = params.get("form") if params is not None else None
        if form_pk:
            try:
                form = DynamicForm.objects.filter(pk=form_pk).only("schema").first()
            except ValidationError:
                # 主键形态非法（非 UUID）与不存在同口径：可读 400，不落 500
                form = None
            if form is None:
                raise RestValidationError(_("The form does not exist"))
            schema = form.schema
        try:
            contains = compile_materialized_filters(value, schema)
        except ValidationError as exc:
            # Django 校验异常落在全局处理器的「未预期异常」分支（500）；筛选参数
            # 错误一律归一为 DRF 校验异常，与其它筛选后端同口径（可读 400）
            messages = getattr(exc, "messages", None) or [str(exc)]
            raise RestValidationError(str(messages[0])) from exc
        return apply_materialized_contains(queryset, contains)
