#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""原地对齐执行器（本阶段范围：表改名 + 可空列补齐）。

为什么先做这两步：它们覆盖了 4.2.5 → 当前的全部**结构**差异（差异报告实测只有 14 张改名表 +
3 张表补列，其余 76 张由 `migrate` 新建），而且 PostgreSQL 的 ``ALTER TABLE ... RENAME TO``
会连带保留数据、索引、序列、**外键约束**——这正是「二开表不需要动」的技术依据。

边界（本阶段不做，属后续批次）：

- **迁移账本重写**（删旧链记录、把当前链补记为已应用）与 ``django_content_type`` / 权限重指；
- **存量字符串重指**（``DataMaskRule.model`` / ``ImportTemplate.target_model`` / 数据集模型串等）；
- **NOT NULL 新增列**：需要回填策略，本阶段只报告不执行（``skipped`` 里给出原因）；
- 列类型变更、旧列多余：报告里带 ``review`` 标记，不自动执行。

因此本模块的定位是**可验证的结构对齐原型**：``dry_run`` 出计划，``apply`` 在单事务内执行
可自动化的步骤，执行后重新比对并报告残留差异。
"""

from __future__ import annotations

import re
from typing import Any

from ops.legacy import schema as legacy_schema
from ops.legacy.structure import (
    complete_structure,
    is_project_migration,
    missing_columns,
    missing_constraints,
    missing_indexes,
    missing_tables,
)

_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _quote(identifier: str) -> str:
    """标识符白名单校验（结构目录来源也应防御，避免拼接出意外 DDL）。"""
    if not _IDENTIFIER_RE.match(identifier):
        raise ValueError(f"非法标识符：{identifier!r}")
    return f'"{identifier}"'


def build_steps(dsn: str, tag: str | None = None) -> dict[str, Any]:
    """把结构差异翻成可执行的 DDL 步骤（只读：不连接目标库做任何写入）。"""
    report = legacy_schema.collect_report(dsn, tag=tag)
    target = legacy_schema.target_schema()
    steps: list[dict[str, Any]] = []
    skipped: list[str] = []
    for source, destination in report["renamed"]:
        steps.append({"step": "rename_table", "from": source, "to": destination})
    for change in report["changes"]:
        table = change["new"]
        for column in change["columns_added"]:
            spec = target.get(table, {}).get(column)
            if spec is None:
                skipped.append(f"{table}.{column}: 目标结构中找不到该列（映射目录待补）")
                continue
            sql_type = spec.get("sql_type") or spec["type"]
            if spec["nullable"]:
                steps.append({"step": "add_column", "table": table, "column": column, "type": sql_type})
                continue
            if spec.get("default_sql"):
                # NOT NULL 但默认值可写成字面量：带默认值加列回填既有行，再收紧约束并去掉服务端默认
                steps.append(
                    {
                        "step": "add_column",
                        "table": table,
                        "column": column,
                        "type": sql_type,
                        "not_null": True,
                        "default_sql": spec["default_sql"],
                    }
                )
                continue
            skipped.append(f"{table}.{column}: 目标列为 NOT NULL 且默认值不可写为字面量（需人工回填策略）")
    for change in report["changes"]:
        for column in change["columns_type_changed"]:
            skipped.append(f"{change['new']}.{column}: 类型变更需人工复核（本阶段不自动执行）")
    return {"report": report, "steps": steps, "skipped": skipped}


def execute_steps(dsn: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """在**单事务**内执行 DDL 步骤（任一步失败整体回滚）。"""
    import psycopg

    executed: list[dict[str, Any]] = []
    skipped: list[str] = []
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor, connection.transaction():
        cursor.execute("select table_name from information_schema.tables where table_schema = 'public'")
        existing = {row[0] for row in cursor.fetchall()}
        for step in steps:
            if step["step"] == "rename_table":
                if step["from"] not in existing:
                    skipped.append(f"{step['from']}: 旧表不存在（可能已对齐或已处理）")
                    continue
                if step["to"] in existing:
                    skipped.append(f"{step['to']}: 目标表已存在，跳过改名以免覆盖")
                    continue
                cursor.execute(f"alter table {_quote(step['from'])} rename to {_quote(step['to'])}")
                existing.discard(step["from"])
                existing.add(step["to"])
                executed.append(step)
            elif step["step"] == "add_column":
                table, column = _quote(step["table"]), _quote(step["column"])
                if step.get("not_null"):
                    # 带默认值加列（回填既有行）→ 收紧为 NOT NULL → 去掉服务端默认，与目标结构一致
                    cursor.execute(
                        f"alter table {table} add column {column} {step['type']} default {step['default_sql']}"
                    )
                    cursor.execute(f"alter table {table} alter column {column} set not null")
                    cursor.execute(f"alter table {table} alter column {column} drop default")
                else:
                    cursor.execute(f"alter table {table} add column {column} {step['type']}")
                executed.append(step)
    return {"executed": executed, "skipped": skipped}


def apply_alignment(dsn: str, tag: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    """结构对齐入口：``dry_run=True`` 只出计划；否则执行并回读验收。"""
    prepared = build_steps(dsn, tag=tag)
    result: dict[str, Any] = {
        "dry_run": dry_run,
        "planned": len(prepared["steps"]),
        "steps": prepared["steps"],
        "skipped": prepared["skipped"],
    }
    if dry_run:
        return result
    outcome = execute_steps(dsn, prepared["steps"])
    result["executed"] = len(outcome["executed"])
    result["skipped"] += outcome["skipped"]
    after = legacy_schema.collect_report(dsn, tag=tag)
    result["after"] = {
        "renamed_left": len(after["renamed"]),
        "unmatched_old": after["unmatched_old"],
        "blockers": after["blockers"],
        "tables_with_changes": len(after["changes"]),
        "changes": after["changes"],
    }
    result["aligned"] = not after["renamed"] and not after["blockers"] and not after["changes"]
    return result


# ---------------------------------------------------------------------------
# 缺失表补齐（用 Django 自己的 CreateModel 操作，保证目标结构就是 migrate 会建出的结构）
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 迁移账本重写
# ---------------------------------------------------------------------------


def rewrite_ledger(dsn: str) -> dict[str, Any]:
    """把旧库的**本仓库 app** 迁移记录对齐到当前代码链（删旧链游离、补记当前链，不执行 DDL）。

    三方 app（auth / celery / token_blacklist 等）的记录不动：它们的迁移是真增量链，
    后续常规 ``migrate`` 会按记录把版本差异（新表/改列）补齐——这些差异也出现在复检报告里。
    """
    import psycopg
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(None, ignore_no_migrations=False)
    current = {
        (app, name) for (app, name), migration in loader.disk_migrations.items() if is_project_migration(migration)
    }
    project_apps = {app for app, _name in current}
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor, connection.transaction():
        cursor.execute("select app, name from django_migrations")
        recorded = {(app, name) for app, name in cursor.fetchall()}
        # 只清理本仓库 app 的旧记录：三方 app 的记录一律不动（它们的增量由常规 migrate 处理）
        stale = sorted(row for row in recorded if row[0] in project_apps and row not in current)
        missing = sorted(current - recorded)
        if stale:
            cursor.executemany("delete from django_migrations where app = %s and name = %s", stale)
        if missing:
            cursor.executemany("insert into django_migrations (app, name, applied) values (%s, %s, now())", missing)
    return {"deleted": len(stale), "recorded": len(missing), "stale": stale[:20], "missing": missing[:20]}


# ---------------------------------------------------------------------------
# 全流程编排
# ---------------------------------------------------------------------------


def align_full(
    dsn: str,
    tag: str | None = None,
    dry_run: bool = True,
    with_references: bool = True,
    with_ledger: bool = True,
) -> dict[str, Any]:
    """完整原地对齐：结构 → 缺失表 → 引用层 → 账本（每步都先预演；阻断项存在则不执行）。"""
    from ops.legacy import refs as legacy_refs

    prepared = build_steps(dsn, tag=tag)
    report = prepared["report"]
    # 改名会把旧表变成目标名，因此「真正要新建」= 目标表 - 现有表 - 改名目标
    rename_targets = {target for _, target in report["renamed"]}
    planned_tables = [table for table in missing_tables(dsn) if table not in rename_targets]
    result: dict[str, Any] = {
        "dry_run": dry_run,
        "blockers": report["blockers"],
        "notes": prepared["report"].get("notes", []),
        "structure": {
            "planned": len(prepared["steps"]),
            "steps": prepared["steps"],
            # 结构阶段按当前模型补列（含可调用默认值），因此这类复核项不再是「未处理」
            "skipped": [item for item in prepared["skipped"] if "不可写为字面量" not in item],
        },
        "missing_tables": planned_tables,
    }
    if report["blockers"]:
        result["refused"] = "存在阻断项（旧表无法唯一映射），拒绝执行；请先在映射目录登记"
        return result
    moves = legacy_refs.content_type_moves(report["renamed"], legacy_schema.auto_created_tables())
    if dry_run:
        result["missing_columns"] = missing_columns(dsn)
        result["missing_indexes"] = missing_indexes(dsn)
        result["missing_constraints"] = missing_constraints(dsn)
        result["content_types"] = legacy_refs.rebind_content_types(dsn, moves, dry_run=True)
        result["references"] = (
            legacy_refs.rewrite_stored_refs(dsn, moves, dry_run=True) if with_references else {"skipped": "未启用"}
        )
        result["ledger"] = {"deleted": None, "recorded": None, "note": "dry-run：不写账本"}
        return result

    outcome = execute_steps(dsn, prepared["steps"])
    result["dropped_tables"] = drop_removed_tables(dsn)
    structure = complete_structure(dsn)
    result["structure"]["executed"] = len(outcome["executed"])
    result["structure"]["skipped"] += outcome["skipped"]
    result["missing_tables"] = structure["tables"] + structure["through_tables"]
    result["missing_fields"] = structure["fields"]
    result["prerequisites"] = structure["prerequisites"]
    result["missing_indexes"] = structure["indexes"]
    result["missing_constraints"] = structure["constraints"]
    result["structure"]["skipped"] += structure["skipped"]
    result["content_types"] = legacy_refs.rebind_content_types(dsn, moves, dry_run=False)
    result["references"] = (
        legacy_refs.rewrite_stored_refs(dsn, moves, dry_run=False) if with_references else {"skipped": "未启用"}
    )
    if with_ledger:
        result["ledger"] = rewrite_ledger(dsn)
    result["after"] = _verify(dsn, tag=tag)
    result["aligned"] = result["after"]["structure_complete"] and not result["after"]["blockers"]
    return result


def drop_removed_tables(dsn: str) -> list[str]:
    """删除 clean cut 后不再存在的旧表（只允许 schema.REMOVED_TABLES 白名单）。"""
    import psycopg
    from psycopg import sql

    from ops.legacy.schema import REMOVED_TABLES

    dropped: list[str] = []
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor, connection.transaction():
        for table in sorted(REMOVED_TABLES):
            cursor.execute("select to_regclass(%s)", (table,))
            if cursor.fetchone()[0] is None:
                continue
            cursor.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(table)))
            dropped.append(table)
    return dropped


def _verify(dsn: str, tag: str | None = None) -> dict[str, Any]:
    """执行后复检：残留改名 / 阻断项 / 缺失表 / 列差异。"""
    after = legacy_schema.collect_report(dsn, tag=tag)
    remaining_tables = missing_tables(dsn)
    return {
        "renamed_left": len(after["renamed"]),
        "blockers": after["blockers"],
        "unmatched_old": after["unmatched_old"],
        "missing_tables": remaining_tables,
        "tables_with_changes": len(after["changes"]),
        "changes": after["changes"],
        "structure_complete": not remaining_tables and not after["renamed"] and not after["blockers"],
    }
