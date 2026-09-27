#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""报表设计（``Report.design``）的规范化与校验。

P2.2 批次二把报表从「固定口径导出」扩为「可设计报表」：

- **明细表**：``columns`` 指定导出/附件列（空 = 数据集全部列），``table_limit`` 限明细行数；
- **聚合组件**：``components`` 为指标卡 / 柱状 / 折线 / 饼图，投递时每个组件落一张独立 sheet；
- **空 design（``{}``）= 存量行为**：全列明细 + 单 sheet，xlsx 与邮件口径完全不变。

本模块是设计载荷的**单一事实源**（序列化器与投递渲染共用）：字段白名单按数据集列过滤、
聚合字段（metric/value_field/group_by/date_trunc）按数据集与数值列校验，未声明键丢弃。
"""

from django.utils.translation import gettext_lazy as _

#: 聚合组件类型（表格即「明细本体」，不做成组件，避免两套表格概念）
REPORT_COMPONENT_TYPES = ("number", "bar", "line", "pie")
#: 聚合口径（与 dataset.utils.dataset.ALLOWED_METRICS 同口径）
REPORT_METRICS = ("count", "sum", "avg")
#: 组件宽度档位（12 = 整行，6 = 半行两列）
REPORT_SPANS = (12, 6)
REPORT_MAX_COMPONENTS = 12
REPORT_MAX_TITLE = 64
REPORT_MAX_ID = 64
REPORT_TABLE_LIMIT_MIN = 10
REPORT_TABLE_LIMIT_MAX = 500
REPORT_TABLE_LIMIT_DEFAULT = 100
#: 时间桶粒度（仅图表；空 = 不按时间分组）
REPORT_DATE_TRUNCS = ("", "day", "month")


class ReportDesignError(ValueError):
    """设计载荷非法（消息可直接回给调用方）。"""


def _as_int(value, field: str, default: int) -> int:
    if value in (None, ""):
        return default
    # bool 是 int 的子类：True 会被静默当成 1，必须显式拒绝
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReportDesignError(_("Invalid report design number: {}").format(field))
    return value


def _normalise_metric(item: dict, dataset_columns, numeric_columns, where: str) -> dict:
    metric = str(item.get("metric") or "count").strip()
    if metric not in REPORT_METRICS:
        raise ReportDesignError(_("Invalid report metric: {}").format(metric))
    value_field = str(item.get("value_field") or "").strip()
    if value_field and value_field not in dataset_columns:
        raise ReportDesignError(_("Unknown report field: {}").format(value_field))
    if metric in ("sum", "avg"):
        if not value_field:
            raise ReportDesignError(_("Value field is required for {} in {}").format(metric, where))
        if value_field not in numeric_columns:
            raise ReportDesignError(_("Value field is not numeric in {}: {}").format(where, value_field))
    return {"metric": metric, "value_field": value_field}


def normalize_report_design(raw, dataset, numeric_columns) -> dict:
    """归一化并校验设计载荷；非法即抛 ``ReportDesignError``。空载荷返回 ``{}``（存量行为）。"""
    if raw in (None, "", {}):
        return {}
    if not isinstance(raw, dict):
        raise ReportDesignError(_("Invalid report design"))

    dataset_columns = [str(item) for item in (getattr(dataset, "columns", None) or [])]
    known = set(dataset_columns)
    numeric = {str(item) for item in (numeric_columns or [])}

    # ---- 明细列：命中数据集列的保序子集；空 = 全部列 ----
    raw_columns = raw.get("columns") or []
    if not isinstance(raw_columns, list):
        raise ReportDesignError(_("Invalid report design columns"))
    columns = []
    for item in raw_columns:
        name = str(item).strip()
        if not name:
            continue
        if name not in known:
            raise ReportDesignError(_("Unknown report column: {}").format(name))
        if name not in columns:
            columns.append(name)

    table_limit = _as_int(raw.get("table_limit"), "table_limit", REPORT_TABLE_LIMIT_DEFAULT)
    if not REPORT_TABLE_LIMIT_MIN <= table_limit <= REPORT_TABLE_LIMIT_MAX:
        raise ReportDesignError(
            _("Report row limit must be {}~{}").format(REPORT_TABLE_LIMIT_MIN, REPORT_TABLE_LIMIT_MAX)
        )

    # ---- 聚合组件 ----
    raw_components = raw.get("components") or []
    if not isinstance(raw_components, list):
        raise ReportDesignError(_("Invalid report design components"))
    if len(raw_components) > REPORT_MAX_COMPONENTS:
        raise ReportDesignError(_("Too many report components (max {})").format(REPORT_MAX_COMPONENTS))

    components = []
    seen_ids = set()
    for index, item in enumerate(raw_components):
        where = _("component {}").format(index + 1)
        if not isinstance(item, dict):
            raise ReportDesignError(_("Invalid report component: {}").format(index + 1))

        component_id = str(item.get("id") or "").strip()
        if not component_id or len(component_id) > REPORT_MAX_ID:
            raise ReportDesignError(_("Invalid report component id: {}").format(index + 1))
        if component_id in seen_ids:
            raise ReportDesignError(_("Duplicated report component id: {}").format(component_id))
        seen_ids.add(component_id)

        component_type = str(item.get("type") or "").strip()
        if component_type not in REPORT_COMPONENT_TYPES:
            raise ReportDesignError(_("Unknown report component type: {}").format(component_type or index + 1))

        title = str(item.get("title") or "").strip()
        if len(title) > REPORT_MAX_TITLE:
            raise ReportDesignError(_("Report component title is too long: {}").format(component_id))

        span = _as_int(item.get("span"), "span", REPORT_SPANS[0])
        if span not in REPORT_SPANS:
            raise ReportDesignError(_("Invalid report component span: {}").format(component_id))

        group_by = str(item.get("group_by") or "").strip()
        date_trunc = str(item.get("date_trunc") or "").strip()
        if component_type == "number":
            if group_by:
                raise ReportDesignError(_("Metric card does not take a group by: {}").format(component_id))
            date_trunc = ""
        else:
            if not group_by:
                raise ReportDesignError(_("Group by is required for {}: {}").format(component_type, component_id))
            if group_by not in known:
                raise ReportDesignError(_("Unknown report group by: {}").format(group_by))
            if date_trunc not in REPORT_DATE_TRUNCS:
                raise ReportDesignError(_("Invalid report date trunc: {}").format(date_trunc))

        component = {
            "id": component_id,
            "type": component_type,
            "span": span,
            **_normalise_metric(item, known, numeric, where),
        }
        if title:
            component["title"] = title
        if group_by:
            component["group_by"] = group_by
        if date_trunc:
            component["date_trunc"] = date_trunc
        components.append(component)

    return {"columns": columns, "table_limit": table_limit, "components": components}


def design_export_columns(design, dataset_columns) -> list:
    """明细导出列：design.columns ∩ 数据集列（保序）；空 design / 空 columns = 数据集全部列。"""
    dataset_columns = [str(item) for item in (dataset_columns or [])]
    if not isinstance(design, dict) or not design.get("columns"):
        return dataset_columns
    known = set(dataset_columns)
    # 数据集列后续被删/改名时不阻断投递：逐列静默跳过（与字段权限失配同口径）
    return [str(item) for item in design["columns"] if str(item) in known]


def design_table_limit(design) -> int:
    """明细行数上限：非法/缺省回落默认值（读取侧宽容，写入侧已由序列化器拦截）。"""
    value = design.get("table_limit") if isinstance(design, dict) else None
    if isinstance(value, bool) or not isinstance(value, int):
        return REPORT_TABLE_LIMIT_DEFAULT
    if not REPORT_TABLE_LIMIT_MIN <= value <= REPORT_TABLE_LIMIT_MAX:
        return REPORT_TABLE_LIMIT_DEFAULT
    return value


def design_components(design) -> list:
    """设计中的聚合组件（读侧宽容：非列表/非 dict 项直接跳过）。"""
    raw = design.get("components") if isinstance(design, dict) else None
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("id") and item.get("type")]
