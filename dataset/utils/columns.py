#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据集列声明解析：模型字段 / JSON 路径（+ 数值类型标注）。

列声明语法（``columns`` / ``filters[].field`` / ``ordering`` / ``group_by`` /
``value_field`` 通用）：

- ``created_time``：模型字段（现状）；
- ``data.kind``：JSON 路径（首段 = 模型上的 JSONField，第二段 = 键）；
- ``data.amount|number``：JSON 路径 + 数值类型标注（sum / avg 与数值比较必需）。

安全边界：类型白名单、段数与字符集、根字段必须是（白名单内的）JSONField、
别名冲突全部 fail-closed；解析入口唯一，保存与执行双侧共用同一口径。
"""

import re
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db.models import FloatField, JSONField, TextField
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Substr
from django.utils.translation import gettext_lazy as _

#: 类型标注白名单：number（数值聚合/比较）与 date（趋势分桶，值契约 YYYY-MM-DD）
JSON_COLUMN_TYPES = ("number", "date")
#: 路径段字符集（与表单设计器生成的字段 key 口径一致）
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ALIAS_PREFIX = "json_"
#: 日期桶的前缀长度（YYYY-MM / YYYY-MM-DD，与模型字段路径的桶名格式一致）
DATE_BUCKET_LENGTH = {"month": 7, "day": 10}


def sanitize(value: str) -> str:
    """ORM 别名净化：JSON 键中的 ``-`` 等字符转为下划线。"""
    return re.sub(r"[^A-Za-z0-9_]", "_", str(value))


@dataclass(frozen=True)
class ColumnSpec:
    """列声明解析结果；``raw``（原始声明）即输出列名，前后端唯一标识。"""

    raw: str
    path: str
    is_json: bool
    root: str
    key: str = ""
    value_type: str = ""

    @property
    def alias(self) -> str:
        """ORM 别名：模型字段用原名；JSON 列净化前缀化，避免与真实字段相撞。"""
        if not self.is_json:
            return self.raw
        return f"{_ALIAS_PREFIX}{sanitize(self.root)}_{sanitize(self.key)}"


def split_type(column) -> tuple:
    """按最后一个 ``|`` 切分类型标注（模型字段名与 JSON 键均不含 ``|``）。"""
    raw = str(column or "").strip()
    if "|" not in raw:
        return raw, ""
    path, __, value_type = raw.rpartition("|")
    return path.strip(), value_type.strip()


def parse_column(model, column, whitelist=None) -> ColumnSpec:
    """解析单列声明；越界一律 ValidationError。

    ``whitelist`` 为模型 DATA 字段白名单（``available_fields`` 结果）：
    非空时模型字段与 JSON 根字段都必须命中（保存 / 执行侧口径同源）。
    """
    raw = str(column or "").strip()
    if not raw:
        raise ValidationError(_("Dataset column cannot be empty"))
    path, value_type = split_type(raw)
    if value_type and value_type not in JSON_COLUMN_TYPES:
        raise ValidationError(_("Unsupported column type: {}").format(value_type))
    if "." not in path:
        if value_type:
            # 模型字段的类型由字段定义判定，不接受手工标注（避免两套数值口径）
            raise ValidationError(_("Column type is only supported for JSON paths: {}").format(raw))
        if whitelist is not None and path not in whitelist:
            raise ValidationError(_("Field {} is not available for datasets").format(path))
        return ColumnSpec(raw=raw, path=path, is_json=False, root=path)

    segments = path.split(".")
    if len(segments) != 2:
        raise ValidationError(_("Only one-level JSON path is supported: {}").format(raw))
    root, key = segments
    if not _SEGMENT_RE.match(root) or not _SEGMENT_RE.match(key):
        raise ValidationError(_("Invalid JSON path: {}").format(raw))
    if whitelist is not None and root not in whitelist:
        raise ValidationError(_("Field {} is not available for datasets").format(root))
    try:
        model_field = model._meta.get_field(root)
    except Exception as exc:  # noqa: BLE001 历史列失配 → 统一文案
        raise ValidationError(_("Field {} is not available for datasets").format(root)) from exc
    if not isinstance(model_field, JSONField):
        raise ValidationError(_("Field {} is not a JSON field: {}").format(root, raw))
    return ColumnSpec(raw=raw, path=path, is_json=True, root=root, key=key, value_type=value_type)


def expression_of(spec: ColumnSpec):
    """JSON 列的 ORM 表达式：统一 Cast 到显式类型。

    文本列也必须 Cast（``output_field=TextField()``）：裸 ``KeyTextTransform`` 参与
    过滤时会继承 JSON 字段语义，比较值被按 JSON 文档准备——SQLite 直接报
    ``malformed JSON``、PG 的语义也不符文本比较预期；``number`` 标注 Cast 为
    ``FloatField`` 以支持数值比较与 sum / avg。
    """
    if not spec.is_json:
        return None
    expression = KeyTextTransform(spec.key, spec.root)
    output_field = FloatField() if spec.value_type == "number" else TextField()
    return Cast(expression, output_field=output_field)


def resolve_columns(model, columns, whitelist=None) -> list:
    """批量解析并检测别名冲突（sanitize 后重名，如 ``data.a-b`` 与 ``data.a_b``）。"""
    specs = [parse_column(model, column, whitelist) for column in columns or []]
    aliases = set()
    for spec in specs:
        if not spec.is_json:
            continue
        if spec.alias in aliases:
            raise ValidationError(_("Duplicated JSON column alias: {}").format(spec.raw))
        aliases.add(spec.alias)
        try:
            model._meta.get_field(spec.alias)
        except Exception:  # noqa: BLE001 未与真实字段冲突 → 正常
            continue
        raise ValidationError(_("JSON column alias conflicts with a model field: {}").format(spec.raw))
    return specs


def annotations_for(specs) -> dict:
    """JSON 列的 ORM 注解映射（别名 → 表达式）；模型字段不产生注解。"""
    return {spec.alias: expression_of(spec) for spec in specs if spec.is_json}


def json_fields_of(model) -> list:
    """模型上的 JSONField 字段名（设计器提示「哪些字段支持 JSON 路径」）。"""
    return sorted(field.name for field in model._meta.get_fields() if isinstance(field, JSONField))


def json_fields_of_bound_model(bound_model) -> list:
    """按 ``label_lower`` 取模型上的 JSONField 名单（模型不可用返回空表）。"""
    from django.apps import apps as django_apps

    try:
        model = django_apps.get_model(*str(bound_model or "").split(".", 1))
    except (LookupError, ValueError):
        return []
    return json_fields_of(model) if model is not None else []


def visible_root_of(spec: ColumnSpec) -> str:
    """字段权限收敛用根字段：JSON 路径列映射到根（``data.*`` → ``data``）。"""
    return spec.root if spec.is_json else spec.raw


def date_bucket_expression(spec: ColumnSpec, date_trunc: str):
    """JSON 日期列的趋势桶：``Substr`` 前缀截断（跨库一致）。

    不用 ``Trunc(Cast(expr, DateTimeField()))``：SQLite 的 ``CAST(x AS datetime)``
    无类型亲和性会数值化，PG 则需显式 ``::timestamp``——两端方言不一致。
    ISO 日期字符串的字典序等于时间序，前缀即桶且桶名与模型字段路径同格式。
    """
    length = DATE_BUCKET_LENGTH.get(date_trunc)
    if not length:
        raise ValidationError(_("Date trunc {} is not allowed").format(date_trunc))
    return Substr(expression_of(spec), 1, length)
