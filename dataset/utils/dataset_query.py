#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据集执行与聚合：行数据 / 图表数据源 / 卡片级布局过滤（自 dataset 拆分，行为不变）。

安全口径与保存侧同源（见 ``dataset.utils.dataset``）：字段白名单 + 行级数据权限 +
字段权限叠加，聚合分组/取值字段必须对浏览者可见（fail-closed 报错）。
"""

from typing import Any

from django.core.exceptions import ValidationError
from django.db.models import Avg, Count, DateTimeField, Sum
from django.db.models.functions import Trunc
from django.utils.translation import gettext_lazy as _

from dataset.utils.columns import date_bucket_expression, expression_of, parse_column, visible_root_of
from dataset.utils.dataset import (
    AGGREGATE_BUCKET_LIMIT,
    ALLOWED_DATE_TRUNC,
    ALLOWED_METRICS,
    ROW_LIMIT_CAP,
    _check_numeric,
    _group_label,
    available_fields,
    build_queryset,
    get_whitelisted_model,
    viewer_visible_fields,
)


def execute_dataset(dataset: Any, user_obj: Any, count_only: bool = False, max_rows: int | None = None) -> Any:
    """执行数据集：返回白名单列的行数据（row_limit 上限）。

    输出列 = 数据集 columns ∩ 浏览者字段权限白名单（JSON 路径列按根字段收敛；
    超管/无字段配置 = 全量，显式授权即收敛）；交集为空返回空结果（不泄露行数等
    任何业务数据）。JSON 列经别名注解后重命名回列声明，行键与列头始终一致。

    ``count_only=True``：数字卡场景只取行数——全量物化 ≤row_limit 行 ×
    全列只为读 total 是纯开销，挂屏 M 张数字卡每刷新周期即 M 次全量行查询；
    此模式跳过列展开与行物化，仅 count。字段权限口径与全量路径一致：浏览者
    无任何可见字段（显式配置为空集）时 total 恒 0，不泄露行数（fail-closed）。

    ``max_rows``：外部行数上限（如报表设计的明细行数上限），与数据集 row_limit
    取小者一并下推到 SQL，避免为 Python 侧切片而全量物化；LIMIT 取行必须有
    确定排序才可复现，数据集与绑定模型均未声明排序时按主键稳定排序。
    """
    queryset, model, columns = build_queryset(dataset, user_obj)
    whitelist = set(available_fields(dataset.bound_model))
    limit = min(int(dataset.row_limit or 1000), ROW_LIMIT_CAP)
    if max_rows is not None:
        limit = min(limit, max_rows)
    if count_only:
        visible = viewer_visible_fields(dataset.bound_model, user_obj)
        if visible is not None and not visible:
            return {"columns": [], "rows": [], "total": 0, "limit": limit}
        return {"columns": [], "rows": [], "total": queryset.count(), "limit": limit}
    specs = [parse_column(model, column, whitelist) for column in columns]
    visible = viewer_visible_fields(dataset.bound_model, user_obj)
    if visible is not None:
        specs = [spec for spec in specs if visible_root_of(spec) in visible]
    if not specs:
        return {"columns": [], "rows": [], "total": 0, "limit": limit}
    alias_map = {spec.alias: spec.raw for spec in specs if spec.alias != spec.raw}
    if max_rows is not None and not queryset.ordered:
        # 行数上限下推到 SQL 后，LIMIT 取行依赖数据库返回顺序则不可复现：
        # 数据集未声明排序且绑定模型无默认排序时按主键稳定取行
        queryset = queryset.order_by("pk")
    rows = list(queryset.values(*[spec.alias for spec in specs])[:limit])
    if alias_map:
        rows = [{alias_map.get(key, key): value for key, value in row.items()} for row in rows]
    return {"columns": [spec.raw for spec in specs], "rows": rows, "total": queryset.count(), "limit": limit}


def aggregate_dataset(
    dataset: Any, user_obj: Any, group_by: Any, metric: Any = "count", date_trunc: Any = None, value_field: Any = None
) -> Any:
    """聚合：图表卡片数据源。输出 [{name, value}]（桶上限 365）。

    - group_by 为空 = 无分组纯聚合（NL 查数「一共有多少个」等）：单桶输出，name 为「总计」；
    - date_trunc（day/month）仅对 DateTime 字段生效：按时间桶分组（趋势），需 group_by；
    - metric: count / sum / avg（sum、avg 仅数值字段，value_field 必填且在白名单）；
    - 字段权限叠加：分组/取值字段必须对浏览者可见，否则聚合结果
      会绕过列白名单泄露隐藏字段（如薪酬求和），fail-closed 报错。
    """
    if metric not in ALLOWED_METRICS:
        raise ValidationError(_("Metric {} is not allowed").format(metric))
    model = get_whitelisted_model(dataset.bound_model)
    whitelist = set(available_fields(dataset.bound_model))
    group_spec = parse_column(model, group_by, whitelist) if group_by else None
    # JSON 趋势列必须带 |date 标注（值契约 YYYY-MM-DD，分桶走 Substr 前缀截断）
    if date_trunc and group_spec is not None and group_spec.is_json and group_spec.value_type != "date":
        raise ValidationError(_("JSON trend column requires |date type: {}").format(group_spec.raw))

    visible = viewer_visible_fields(dataset.bound_model, user_obj)
    if visible is not None:
        if group_spec is not None and visible_root_of(group_spec) not in visible:
            raise ValidationError(_("No field permission for {}.{}").format(dataset.bound_model, group_spec.raw))
        if value_field:
            value_visible = parse_column(model, value_field, whitelist)
            if visible_root_of(value_visible) not in visible:
                raise ValidationError(_("No field permission for {}.{}").format(dataset.bound_model, value_visible.raw))

    queryset, __, __ = build_queryset(dataset, user_obj, extra_filters=[])
    annotation = Count("pk")
    if metric in ("sum", "avg"):
        if not value_field:
            raise ValidationError(_("Value field is required for aggregation"))
        value_spec = _check_numeric(model, value_field, whitelist)
        # 模型字段用字段名引用；JSON 路径用 Cast 表达式（数值比较与聚合同一口径）
        expression = expression_of(value_spec) or value_spec.root
        annotation = Sum(expression) if metric == "sum" else Avg(expression)

    if not group_by:
        # 无分组纯聚合：单桶（趋势/分组语义不成立，date_trunc 忽略）
        aggregated = queryset.aggregate(agg_value=annotation).get("agg_value")
        return {
            "name": "",
            "metric": metric,
            "series": [{"name": str(_("Total")), "value": aggregated if aggregated is not None else 0}],
        }

    if group_spec is None:
        # 分组字段已由 parse_column 校验（非法即抛错），此处仅收窄类型、同文案兜底
        raise ValidationError(_("Field {} is not available for datasets").format(group_by))

    if date_trunc:
        if date_trunc not in ALLOWED_DATE_TRUNC:
            raise ValidationError(_("Date trunc {} is not allowed").format(date_trunc))
        if group_spec.is_json:
            # JSON 日期列：Substr 前缀即桶（桶名与模型字段路径同格式，无需 Python 再格式化）
            rows = (
                queryset.annotate(bucket_name=date_bucket_expression(group_spec, date_trunc))
                .values("bucket_name")
                .annotate(agg_value=annotation)
                .order_by("bucket_name")
                .values("bucket_name", "agg_value")[:AGGREGATE_BUCKET_LIMIT]
            )
            return {
                "name": group_by,
                "metric": metric,
                "series": [
                    {
                        "name": row["bucket_name"] or "",
                        "value": row["agg_value"] if row["agg_value"] is not None else 0,
                    }
                    for row in rows
                ],
            }
        model_field = model._meta.get_field(group_spec.root)
        if not isinstance(model_field, DateTimeField):
            raise ValidationError(_("Field {} is not a datetime, cannot trend").format(group_spec.raw))
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
        # 分组标签走字段元数据映射：布尔 → 启用/禁用、choices → display 文案；
        # JSON 路径列无字段元数据 → 原始值（_group_label(value, None) 同口径）
        group_field = None if group_spec.is_json else model._meta.get_field(group_spec.root)
        queryset = queryset.values(group_spec.alias).annotate(agg_value=annotation).order_by("-agg_value")
        rows = queryset.values(group_spec.alias, "agg_value")[:AGGREGATE_BUCKET_LIMIT]
        series = [
            {
                "name": _group_label(row[group_spec.alias], group_field),
                "value": row["agg_value"] if row["agg_value"] is not None else 0,
            }
            for row in rows
        ]
    return {"name": group_by, "metric": metric, "series": series}


def filter_layout_for_user(layout: Any, user: Any) -> list[Any]:
    """卡片级权限过滤（仪表盘读取侧）：allowed_roles 空 = 全员可见；非空要求浏览者命中其一。

    - 超管全量可见（旁路，与数据权限口径一致）；
    - 匿名/未认证 → 空布局（fail-closed，路由层已拦截，此处兜底）。
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return []
    cards = list(layout or [])
    if getattr(user, "is_superuser", False):
        return cards
    role_codes = set(user.roles.filter(is_active=True).values_list("code", flat=True))
    return [
        card for card in cards if not card.get("allowed_roles") or role_codes & set(card.get("allowed_roles") or [])
    ]
