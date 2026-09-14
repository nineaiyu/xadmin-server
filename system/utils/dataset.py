#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据集执行服务：白名单校验 + ORM 查询构建 + 数据权限过滤。

安全边界（保存与执行双侧校验，禁原生 SQL）：
- 模型白名单 = ModelLabelField DATA 根节点（与数据权限编辑器同源）；
- 字段白名单 = 对应模型的 DATA 子节点；过滤字段/排序字段/聚合字段同样受限；
- op 白名单固定九种；聚合 metric 限 count/sum/avg，sum/avg 仅数值字段；
- 行级过滤走既有入口 `get_filter_queryset`（fail-closed：无授权 → none()）。
"""

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db.models import Avg, Count, DateTimeField, Sum
from django.db.models.functions import Trunc
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)

ALLOWED_OPS = ("exact", "in", "gte", "gt", "lte", "lt", "contains", "startswith", "isnull")
ALLOWED_METRICS = ("count", "sum", "avg")
ALLOWED_DATE_TRUNC = ("day", "month")
AGGREGATE_BUCKET_LIMIT = 365
ROW_LIMIT_CAP = 5000
NUMERIC_FIELD_CLASSES = ("IntegerField", "BigIntegerField", "SmallIntegerField", "FloatField", "DecimalField")


def available_models() -> list:
    """模型白名单：ModelLabelField DATA 根节点（label_lower 列表）。"""
    from system.models import ModelLabelField

    return list(
        ModelLabelField.objects.filter(field_type=ModelLabelField.FieldChoices.DATA, parent__isnull=True).values_list(
            "name", flat=True
        )
    )


def available_fields(bound_model: str) -> list:
    """模型字段白名单：该模型 DATA 节点的子节点字段名。"""
    from system.models import ModelLabelField

    return list(
        ModelLabelField.objects.filter(
            field_type=ModelLabelField.FieldChoices.DATA, parent__name=bound_model
        ).values_list("name", flat=True)
    )


def get_whitelisted_model(bound_model: str):
    """白名单校验 + 取模型类；越权模型 raise ValidationError。"""
    if bound_model not in available_models():
        raise ValidationError(_("Model {} is not available for datasets").format(bound_model))
    try:
        return apps.get_model(*bound_model.split(".", 1))
    except (LookupError, ValueError) as exc:
        raise ValidationError(_("Model {} is not available for datasets").format(bound_model)) from exc


def _check_fields(bound_model: str, fields, allow_empty=True):
    whitelist = set(available_fields(bound_model))
    for field in fields or []:
        if field not in whitelist:
            raise ValidationError(_("Field {}.{} is not available for datasets").format(bound_model, field))
    if not allow_empty and not fields:
        raise ValidationError(_("Dataset columns cannot be empty"))


def validate_filters(bound_model: str, filters):
    """filters: [{field, op, value}]；field 在白名单、op 在白名单、value 形态合法。"""
    if not filters:
        return
    if not isinstance(filters, list):
        raise ValidationError(_("Invalid dataset filters"))
    whitelist = set(available_fields(bound_model))
    for item in filters:
        if not isinstance(item, dict) or not item.get("field") or not item.get("op"):
            raise ValidationError(_("Invalid dataset filters"))
        if item["field"] not in whitelist:
            raise ValidationError(_("Field {}.{} is not available for datasets").format(bound_model, item["field"]))
        if item["op"] not in ALLOWED_OPS:
            raise ValidationError(_("Filter op {} is not allowed").format(item["op"]))
        if item["op"] == "in" and not isinstance(item.get("value"), (list, tuple)):
            raise ValidationError(_("Filter op in requires a list value"))
        if item["op"] == "isnull" and not isinstance(item.get("value"), bool):
            raise ValidationError(_("Filter op isnull requires a boolean value"))


def validate_dataset(instance) -> None:
    """保存侧整体校验（模型/列/过滤/排序/limit/config）。"""
    get_whitelisted_model(instance.bound_model)
    _check_fields(instance.bound_model, instance.columns, allow_empty=False)
    validate_filters(instance.bound_model, instance.filters)
    if instance.ordering:
        field = instance.ordering.lstrip("-")
        if field not in (instance.columns or []):
            raise ValidationError(_("Ordering field must be in columns"))
    if not (0 < int(instance.row_limit) <= ROW_LIMIT_CAP):
        raise ValidationError(_("Row limit must be between 1 and {}").format(ROW_LIMIT_CAP))
    date_field = (instance.config or {}).get("date_field")
    if date_field and date_field not in available_fields(instance.bound_model):
        raise ValidationError(_("Field {}.{} is not available for datasets").format(instance.bound_model, date_field))


def _group_label(value) -> str:
    """分组名：仅 None 落空串；False/0 等合法 falsy 分组值保留字符串形态。"""
    return "" if value is None else str(value)


def _check_numeric(model, field: str):
    try:
        model_field = model._meta.get_field(field)
    except Exception as exc:
        raise ValidationError(_("Field {} is not available for datasets").format(field)) from exc
    if model_field.__class__.__name__ not in NUMERIC_FIELD_CLASSES:
        raise ValidationError(_("Field {} is not numeric, cannot aggregate").format(field))


def build_queryset(dataset, user_obj, extra_filters=None):
    """执行侧查询构建：白名单复核 → filters → 排序 → 数据权限过滤（fail-closed）。"""
    model = get_whitelisted_model(dataset.bound_model)
    _check_fields(dataset.bound_model, dataset.columns, allow_empty=False)
    validate_filters(dataset.bound_model, dataset.filters)

    columns = [str(col) for col in (dataset.columns or [])]
    queryset = model.objects.all()
    conditions = list(dataset.filters or []) + list(extra_filters or [])
    for item in conditions:
        queryset = queryset.filter(**{f"{item['field']}__{item['op']}": item.get("value")})
    if dataset.ordering:
        queryset = queryset.order_by(dataset.ordering)
    # 行级数据权限：fail-closed 继承数据权限编译器（无授权 → none()）
    from common.core.filter import get_filter_queryset

    return get_filter_queryset(queryset, user_obj), model, columns


def execute_dataset(dataset, user_obj):
    """执行数据集：返回白名单列的行数据（row_limit 上限）。"""
    queryset, model, columns = build_queryset(dataset, user_obj)
    limit = min(int(dataset.row_limit or 1000), ROW_LIMIT_CAP)
    rows = list(queryset.values(*columns)[:limit])
    return {"columns": columns, "rows": rows, "total": queryset.count(), "limit": limit}


def aggregate_dataset(dataset, user_obj, group_by, metric="count", date_trunc=None, value_field=None):
    """聚合：图表卡片数据源。输出 [{name, value}]（桶上限 365）。

    - date_trunc（day/month）仅对 DateTime 字段生效：按时间桶分组（趋势）；
    - metric: count / sum / avg（sum、avg 仅数值字段，value_field 必填且在白名单）。
    """
    if metric not in ALLOWED_METRICS:
        raise ValidationError(_("Metric {} is not allowed").format(metric))
    model = get_whitelisted_model(dataset.bound_model)
    whitelist = set(available_fields(dataset.bound_model))
    if group_by not in whitelist:
        raise ValidationError(_("Field {}.{} is not available for datasets").format(dataset.bound_model, group_by))

    queryset, __, __ = build_queryset(dataset, user_obj, extra_filters=[])
    annotation = Count("pk")
    if metric in ("sum", "avg"):
        if not value_field or value_field not in whitelist:
            raise ValidationError(
                _("Field {}.{} is not available for datasets").format(dataset.bound_model, value_field)
            )
        _check_numeric(model, value_field)
        annotation = Sum(value_field) if metric == "sum" else Avg(value_field)

    if date_trunc:
        if date_trunc not in ALLOWED_DATE_TRUNC:
            raise ValidationError(_("Date trunc {} is not allowed").format(date_trunc))
        model_field = model._meta.get_field(group_by)
        if not isinstance(model_field, DateTimeField):
            raise ValidationError(_("Field {} is not a datetime, cannot trend").format(group_by))
        # Django 6：values(kw=注解别名) 的字符串引用被拒，统一用位置式 values；
        # 必须先 values(桶) 再 annotate 聚合（GROUP BY 桶），顺序颠倒会按
        # (桶, 聚合值) 联合分组——每桶裂成多行，趋势图出现重复数据点
        fmt = "%Y-%m" if date_trunc == "month" else "%Y-%m-%d"
        rows = (
            queryset.annotate(bucket_name=Trunc(group_by, date_trunc))
            .values("bucket_name")
            .annotate(agg_value=annotation)
            .order_by("bucket_name")
            .values("bucket_name", "agg_value")[:AGGREGATE_BUCKET_LIMIT]
        )
        series = [
            {
                "name": row["bucket_name"].strftime(fmt) if row["bucket_name"] else "",
                "value": row["agg_value"] if row["agg_value"] is not None else 0,
            }
            for row in rows
        ]
    else:
        queryset = queryset.values(group_by).annotate(agg_value=annotation).order_by("-agg_value")
        rows = queryset.values(group_by, "agg_value")[:AGGREGATE_BUCKET_LIMIT]
        series = [
            {"name": _group_label(row[group_by]), "value": row["agg_value"] if row["agg_value"] is not None else 0}
            for row in rows
        ]
    return {"name": group_by, "metric": metric, "series": series}
