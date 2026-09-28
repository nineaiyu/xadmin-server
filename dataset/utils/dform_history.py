#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""表单 schema 版本历史：提交侧「当时 schema」解析与历史字段合并。

背景：提交只存 ``schema_version``（不存 schema 快照），而 ``DynamicForm.schema_history``
保留最近 N 版全文——表单改版（删/改字段）后，历史提交里被删字段的值仍在 ``data`` 中，
但导出列与详情渲染若只取当前 schema 就会「值还在、看不见」。本模块提供三条口径：

- ``schema_for_version``：按提交版本取 schema（快照优先，缺失回落当前版本）；
- ``merged_fields`` / ``merged_fields_of_forms``：当前 schema ∪ 历史版本中已删除的字段
  （历史字段 label 追加标注），用于导出列与「表单数据」列表动态列——改版后回看/导出
  历史字段不再丢列；
- ``submission_schema``：单条提交的详情渲染口径（提交版 schema ∪ data 中无法识别的键）。

版本快照超出保留窗口（``MAX_SCHEMA_HISTORY``）时按「未知键」兜底，至少保证值可见。
"""

from django.utils.translation import gettext_lazy as _

#: 历史字段的列名标注（导出列头 / 列表列头 / 详情字段名一致）
HISTORICAL_MARK = _(" (historical)")


def key_of(item) -> str:
    """字段标识：schema 字段项 → key（非 dict / 缺 key 返回空串）。"""
    if not isinstance(item, dict):
        return ""
    return str(item.get("key") or "").strip()


def fields_of(schema) -> list:
    """schema 载荷 → 合法字段清单（非 dict / 缺 key 项跳过）。"""
    if not isinstance(schema, dict):
        return []
    return [item for item in (schema.get("fields") or []) if key_of(item)]


def _as_historical(item: dict) -> dict:
    """历史字段：原字段定义 + label 标注 + historical 标记（不修改原载荷）。"""
    field = dict(item)
    label = str(field.get("label") or field.get("key") or "")
    field["label"] = f"{label}{HISTORICAL_MARK}"
    field["historical"] = True
    return field


def schema_for_version(form, version) -> dict:
    """按提交版本取 schema：命中快照返回快照，否则回落当前 schema。"""
    try:
        target = int(version)
    except (TypeError, ValueError):
        return dict(form.schema or {})
    if target and target != int(form.schema_version or 1):
        for item in form.schema_history or []:
            if not isinstance(item, dict):
                continue
            try:
                snapshot_version = int(item.get("version") or 0)
            except (TypeError, ValueError):
                continue
            if snapshot_version == target:
                return dict(item.get("schema") or {})
    return dict(form.schema or {})


def merged_fields(form) -> list:
    """当前 schema 字段 + 历史快照中当前已删除的字段（历史字段追加标注）。

    顺序：当前 schema 原序在前，历史字段按版本从新到旧补在末尾（key 去重）。
    """
    fields = fields_of(form.schema)
    seen = {key_of(item) for item in fields}
    for snapshot in form.schema_history or []:
        if not isinstance(snapshot, dict):
            continue
        for item in fields_of(snapshot.get("schema")):
            key = key_of(item)
            if key in seen:
                continue
            seen.add(key)
            fields.append(_as_historical(item))
    return fields


def merged_fields_of_forms(forms) -> list:
    """跨表单合并（导出口径）：按表单顺序拼接并标注历史字段，key 去重保序。"""
    seen, fields = set(), []
    for form in forms:
        for item in merged_fields(form):
            key = key_of(item)
            if not key or key in seen:
                continue
            seen.add(key)
            fields.append(item)
    return fields


def submission_schema(obj) -> list:
    """单条提交的详情渲染口径：提交版 schema ∪ data 中无法识别的键（兜底可见）。

    当前 schema 仍存在的字段保持原样，已删除字段标注历史；快照缺失（版本超出保留
    窗口）时以当前 schema 为底，data 中的其余键按「未知键」补出（值确定可见）。
    """
    if not obj.form_id:
        return []
    form = obj.form
    current_keys = {key_of(item) for item in fields_of(form.schema)}
    fields = [
        dict(item) if key_of(item) in current_keys else _as_historical(item)
        for item in fields_of(schema_for_version(form, obj.schema_version))
    ]
    known = {key_of(item) for item in fields}
    for key in obj.data or {}:
        name = str(key)
        if name in known:
            continue
        fields.append({"key": name, "label": f"{name}{HISTORICAL_MARK}", "type": "input", "historical": True})
        known.add(name)
    return fields
