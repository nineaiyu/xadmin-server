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

from typing import Any

from django.utils.translation import gettext_lazy as _

#: 历史字段的列名标注（导出列头 / 列表列头 / 详情字段名一致）
HISTORICAL_MARK = _(" (historical)")


def key_of(item: Any) -> str:
    """字段标识：schema 字段项 → key（非 dict / 缺 key 返回空串）。"""
    if not isinstance(item, dict):
        return ""
    return str(item.get("key") or "").strip()


def fields_of(schema: Any) -> list[Any]:
    """schema 载荷 → 合法字段清单（非 dict / 缺 key 项跳过）。"""
    if not isinstance(schema, dict):
        return []
    return [item for item in (schema.get("fields") or []) if key_of(item)]


def _as_historical(item: dict[str, Any]) -> dict[str, Any]:
    """历史字段：原字段定义 + label 标注 + historical 标记（不修改原载荷）。"""
    field = dict(item)
    label = str(field.get("label") or field.get("key") or "")
    field["label"] = f"{label}{HISTORICAL_MARK}"
    field["historical"] = True
    return field


def schema_for_version(form: Any, version: Any) -> dict[str, Any]:
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


def merged_fields(form: Any) -> list[Any]:
    """当前 schema 字段 + 历史快照中当前已删除的字段（历史字段追加标注）。

    顺序：当前 schema 原序在前，历史字段按版本从新到旧补在末尾（key 去重）。

    敏感性按「任一版本敏感即敏感」合并：字段由敏感改为非敏感后，历史提交里
    的旧值仍是敏感数据——导出口径若跟随当前 schema，会把这些值明文导出
    （列表/详情按提交快照掩码，导出成了旁路）。
    """
    fields = fields_of(form.schema)
    sensitivity = {key_of(item): bool(item.get("sensitive")) for item in fields}
    seen = set(sensitivity)
    for snapshot in form.schema_history or []:
        if not isinstance(snapshot, dict):
            continue
        for item in fields_of(snapshot.get("schema")):
            key = key_of(item)
            sensitivity[key] = sensitivity.get(key, False) or bool(item.get("sensitive"))
            if key in seen:
                continue
            seen.add(key)
            fields.append(_as_historical(item))
    return [{**item, "sensitive": sensitivity.get(key_of(item), bool(item.get("sensitive")))} for item in fields]


def merged_fields_of_forms(forms: Any) -> list[Any]:
    """跨表单合并（导出口径）：按表单顺序拼接并标注历史字段，key 去重保序。

    同 key 在不同表单的敏感性可能不同（A 表单敏感、B 表单普通）：合并后
    按「任一表单敏感即敏感」处理，导出列不会因为先遇到普通表单而明文。
    """
    seen: dict[str, int] = {}
    fields: list[Any] = []
    for form in forms:
        for item in merged_fields(form):
            key = key_of(item)
            if not key:
                continue
            if key in seen:
                index = seen[key]
                if item.get("sensitive"):
                    fields[index] = {**fields[index], "sensitive": True}
                continue
            seen[key] = len(fields)
            fields.append(item)
    return fields


def snapshot_resolvable(obj: Any) -> bool:
    """提交版本能否解析到 schema 快照（= 当前版本，或命中历史窗口内快照行）。

    版本真值且超出保留窗口（``MAX_SCHEMA_HISTORY``）时返回 False——掩码侧对这类
    提交按 fail-closed 处理（敏感性无法确认的键一律按敏感掩码），历史敏感键不因
    版本滚出快照窗口而明文。版本为空（版本化之前的行）维持「回退当前 schema」
    既有语义，按可解析处理。
    """
    if not getattr(obj, "form_id", None):
        return True
    form = obj.form
    try:
        version = int(obj.schema_version)
    except (TypeError, ValueError):
        return True
    if version and version == int(form.schema_version or 1):
        return True
    for item in form.schema_history or []:
        if not isinstance(item, dict):
            continue
        try:
            snapshot_version = int(item.get("version") or 0)
        except (TypeError, ValueError):
            continue
        if snapshot_version == version:
            return True
    return False


def submission_schema(obj: Any) -> list[Any]:
    """单条提交的详情渲染口径：提交版 schema ∪ data 中无法识别的键（兜底可见）。

    当前 schema 仍存在的字段保持原样，已删除字段标注历史；快照缺失（版本超出保留
    窗口）时以当前 schema 为底，data 中的其余键按「未知键」补出——fail-closed：
    该场景下敏感性无法确认，未知键补 ``sensitive`` 标记（掩码链路按敏感处理）。
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
    fail_closed = not snapshot_resolvable(obj)
    for key in obj.data or {}:
        name = str(key)
        if name in known:
            continue
        fallback = {"key": name, "label": f"{name}{HISTORICAL_MARK}", "type": "input", "historical": True}
        if fail_closed:
            fallback["sensitive"] = True
        fields.append(fallback)
        known.add(name)
    return fields
