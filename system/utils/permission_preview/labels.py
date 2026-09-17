#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""权限可视化：规则值与名称的中文标签工具。"""

import json

from system.models import ModelLabelField


def _humanize_seconds(seconds: int) -> str:
    """相对时间秒数 → 可读时长（1 天 / 2 小时 / 30 分钟）。"""
    seconds = abs(int(seconds))
    if seconds % 86400 == 0:
        return f"{seconds // 86400} 天"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} 小时"
    if seconds % 60 == 0:
        return f"{seconds // 60} 分钟"
    return f"{seconds} 秒"


def _model_label(table: str, label_cache: dict | None = None) -> str:
    """model label_lower → 中文模型名（数据权限注册表），未注册回退原名。

    label_cache 为请求内共享缓存（同一次预览里同一张表只查一次），避免逐规则 N+1。
    """
    if label_cache is not None and table in label_cache["model"]:
        return label_cache["model"][table]
    node = ModelLabelField.objects.filter(parent__isnull=True, name=table).first()
    label = node.label if node else table
    if label_cache is not None:
        label_cache["model"][table] = label
    return label


def _field_label(table: str, field: str, label_cache: dict | None = None) -> str:
    """数据权限规则字段的中文标签（注册表父子树），未注册回退原名。"""
    key = f"{table}__{field}"
    if label_cache is not None and key in label_cache["field"]:
        return label_cache["field"][key]
    node = ModelLabelField.objects.filter(parent__name=table, name=field).first()
    label = node.label if node else field
    if label_cache is not None:
        label_cache["field"][key] = label
    return label


def _new_label_cache() -> dict:
    """一次预览内的 label 缓存（decode_data_permission 内部贯穿传递）。"""
    return {"model": {}, "field": {}}


def _pks_of(value) -> list:
    """table.*.ids 规则的 value 兼容解析：['pk', {'pk': ..}] → [pk]；脏值返回空列表。"""
    try:
        items = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return []
    if not isinstance(items, (list, tuple)):
        return []
    return [(item.get("pk") if isinstance(item, dict) else item) for item in items if item is not None]


def _join_names(names, total: int | None = None) -> str:
    """名称列表 → 「、」连接；超出展示上限时截断并标注总数（预览只读展示，不拉全量）。"""
    names = [str(name) for name in names if name]
    if not names:
        return "（无）"
    if total is not None and total > len(names):
        return "、".join(names) + f" 等 {total} 项"
    return "、".join(names)
