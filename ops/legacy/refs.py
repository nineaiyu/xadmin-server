#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""记录与引用层改写（原地升级）：`django_content_type` 重指 + 存量字符串重指。

结构对齐（改名 / 补列 / 建缺失表）解决「表怎么放」，本模块解决「引用怎么写」：

1. **``django_content_type`` 重指**：域切分让模型换了 ``app_label``（``system.userinfo`` →
   ``identity.userinfo``），旧库里的 content type 行必须先重指，否则 ``post_migrate`` 会为同一模型
   再建一份新行、而 ``auth_permission`` 仍旧挂在旧行上——表现为「权限点看着在、实际不生效」。
   ``auth_permission`` 通过 ``content_type_id`` 关联，重指 ``app_label`` 后其行内引用自然跟随。
2. **存量字符串重指**：数据库里以「模型标签」形态保存的引用（字段树的 ``ModelLabelField.name``、
   数据权限规则的 ``rules[].table`` 等）必须同步改写，否则升级后脱敏/数据权限/导入模板会指向不存在的模型。

映射来源与结构对齐同源：**只按已确认的表改名推导**（``system_userinfo`` → ``identity_userinfo``
即 ``system.userinfo`` → ``identity.userinfo``），自动 M2M 表排除在外（它们没有 content type 行）。
两条铁律：改写一律**可预演**（默认只统计影响行数）；改完必须**回扫残留**（找不到的旧标签要报出来）。
"""

from __future__ import annotations

import re
from typing import Any

# 存量字符串引用的登记表：表 / 列 / 形态
#   label      —— 字符串本身是模型标签（`system.userinfo` 或 `system.userinfo.username`）
#   label_json —— JSON 数组，元素的指定键是模型标签（如数据权限规则的 `rules[].table`）
STORED_REF_TARGETS: tuple[dict[str, Any], ...] = (
    {"table": "system_modellabelfield", "column": "name", "mode": "label"},
    {"table": "system_datapermission", "column": "rules", "mode": "label_json", "keys": ("table",)},
    {"table": "audit_datamaskrule", "column": "model", "mode": "label"},
    {"table": "task_importtemplate", "column": "model", "mode": "label"},
)


def content_type_moves(renamed: list[tuple[str, str]], auto_tables: set[str]) -> dict[str, str]:
    """由表改名推导模型标签迁移：``system.userinfo`` → ``identity.userinfo``。"""
    moves: dict[str, str] = {}
    for old_table, new_table in renamed:
        if new_table in auto_tables:
            continue  # 自动 M2M 表没有 content type 行
        old_app, _, old_model = old_table.partition("_")
        new_app, _, new_model = new_table.partition("_")
        if not old_model or not new_model:
            continue
        if (old_app, old_model) != (new_app, new_model):
            moves[f"{old_app}.{old_model}"] = f"{new_app}.{new_model}"
    return moves


def _alive_tables(cursor: Any) -> set[str]:
    cursor.execute("select table_name from information_schema.tables where table_schema = 'public'")
    return {row[0] for row in cursor.fetchall()}


def _columns_of(cursor: Any, table: str) -> set[str]:
    cursor.execute(
        "select column_name from information_schema.columns where table_schema = 'public' and table_name = %s",
        (table,),
    )
    return {row[0] for row in cursor.fetchall()}


def rebind_content_types(dsn: str, moves: dict[str, str], dry_run: bool = True) -> dict[str, Any]:
    """把模型迁移对应的 ``django_content_type.app_label`` 重指到新域。"""
    import psycopg
    from django.apps import apps

    known_labels = {config.label for config in apps.get_app_configs()}
    result: dict[str, Any] = {"dry_run": dry_run, "moves": moves, "updated": 0, "missing": [], "stale": []}
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        for old_label, new_label in moves.items():
            old_app, _, old_model = old_label.partition(".")
            new_app, _, new_model = new_label.partition(".")
            cursor.execute(
                "select id from django_content_type where app_label = %s and model = %s",
                (old_app, old_model),
            )
            if cursor.fetchone() is None:
                result["missing"].append(old_label)
                continue
            if not dry_run:
                cursor.execute(
                    "update django_content_type set app_label = %s where app_label = %s and model = %s",
                    (new_app, old_app, old_model),
                )
                result["updated"] += cursor.rowcount
        cursor.execute("select distinct app_label from django_content_type order by app_label")
        for (label,) in cursor.fetchall():
            if label not in known_labels:
                result["stale"].append(label)
        if not dry_run:
            connection.commit()
    return result


def _rewrite_label_value(value: str, moves: dict[str, str]) -> str:
    """模型标签字符串重指（``system.userinfo`` / ``system.userinfo.username``）。"""
    for old_label, new_label in moves.items():
        if value == old_label or value.startswith(f"{old_label}."):
            return new_label + value[len(old_label) :]
    return value


def _label_pattern(old_label: str) -> str:
    return "^" + re.escape(old_label) + r"(\..*)?$"


def rewrite_stored_refs(
    dsn: str,
    moves: dict[str, str],
    targets: tuple[dict[str, Any], ...] = STORED_REF_TARGETS,
    dry_run: bool = True,
) -> dict[str, Any]:
    """按登记表改写库内的模型标签字符串；未命中的登记项与残留旧标签都报出来。"""
    import psycopg

    result: dict[str, Any] = {"dry_run": dry_run, "targets": [], "skipped": [], "leftovers": []}
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        alive = _alive_tables(cursor)
        for target in targets:
            table, column, mode = target["table"], target["column"], target["mode"]
            key = f"{table}.{column}"
            if table not in alive:
                result["skipped"].append(f"{key}: 该表在当前库中不存在（旧版本没有此功能）")
                continue
            if column not in _columns_of(cursor, table):
                result["skipped"].append(f"{key}: 该表没有这一列（登记项与当前结构不符，需核对）")
                continue
            if mode == "label":
                changed = _rewrite_label_column(cursor, moves, table, column, dry_run)
            else:
                changed = _rewrite_label_json(cursor, moves, table, column, target.get("keys", ()), dry_run)
            result["targets"].append({"target": key, "changed": changed})
        for old_label in moves:
            for target in targets:
                table, column = target["table"], target["column"]
                if table not in alive:
                    continue
                cursor.execute(
                    f'select count(*) from "{table}" where "{column}"::text ~ %s',  # noqa: S608 - 标识符来自登记表
                    (_label_pattern(old_label),),
                )
                count = (cursor.fetchone() or (0,))[0]  # count(*) 聚合必有行；None 兜底防存根误报
                if count:
                    result["leftovers"].append(f"{table}.{column} 仍有 {count} 行引用 {old_label}")
        if not dry_run:
            connection.commit()
    return result


def _rewrite_label_column(cursor: Any, moves: dict[str, str], table: str, column: str, dry_run: bool) -> int:
    """单列模型标签改写（整列正则替换，避免逐行往返）。"""
    changed = 0
    for old_label, new_label in moves.items():
        if dry_run:
            cursor.execute(
                f'select count(*) from "{table}" where "{column}" ~ %s',  # noqa: S608
                (_label_pattern(old_label),),
            )
            changed += (cursor.fetchone() or (0,))[0]  # count(*) 聚合必有行；None 兜底防存根误报
            continue
        cursor.execute(
            f'update "{table}" set "{column}" = regexp_replace("{column}", %s, %s) where "{column}" ~ %s',  # noqa: S608
            (_label_pattern(old_label), new_label + r"\1", _label_pattern(old_label)),
        )
        changed += cursor.rowcount
    return changed


def _rewrite_label_json(
    cursor: Any, moves: dict[str, str], table: str, column: str, keys: list[str], dry_run: bool
) -> int:
    """JSON 数组内指定键的模型标签改写（数据权限规则这类小表，逐行处理更直观）。"""
    changed = 0
    cursor.execute(f'select id, "{column}" from "{table}" where "{column}" is not null')  # noqa: S608
    for row_id, payload in cursor.fetchall():
        if not isinstance(payload, list):
            continue
        touched = False
        for item in payload:
            if not isinstance(item, dict):
                continue
            for key in keys:
                value = item.get(key)
                if isinstance(value, str):
                    rewritten = _rewrite_label_value(value, moves)
                    if rewritten != value:
                        item[key] = rewritten
                        touched = True
        if not touched:
            continue
        changed += 1
        if not dry_run:
            import json

            cursor.execute(
                f'update "{table}" set "{column}" = %s::jsonb where id = %s',  # noqa: S608
                (json.dumps(payload, ensure_ascii=False), row_id),
            )
    return changed
