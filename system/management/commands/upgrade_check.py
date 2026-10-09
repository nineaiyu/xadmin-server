#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""升级前体检（upgrade_check）：迁移记录对账 + 表结构体检 + 升级路径判定（只读）。

迁移链经过清库重建式合并后，旧版部署的 ``django_migrations`` 记录可能与当前代码
链不兼容（记录名不存在于代码中），或同名迁移的内容已变化导致库结构漂移——直接
执行 ``migrate`` 会造成状态错乱。本命令在升级前给出判定与处置建议：

- ``up-to-date``：迁移记录与表结构均与当前代码一致；
- ``needs-migrate``：存在未应用迁移（含全新空库），执行 ``manage.py migrate`` 即可；
- ``schema-drift``：迁移记录完整但表/列缺失（同名迁移内容已变化的漂移库）——人工排查；
- ``legacy-chain``：存在不在当前代码链中的迁移记录（旧版链路的库）——不支持原地升级，
  按 ``docs/ops/upgrade-stock.md`` 走「备份 + 清库重建 + 数据回灌」。

退出码：0 = 可继续升级（up-to-date / needs-migrate）；1 = 需人工介入。
"""

import json

from django.core.management.base import BaseCommand

DETAIL_LIMIT = 10

VERDICTS = {
    "up-to-date": ("库已与当前代码一致，无需迁移。", None),
    "needs-migrate": ("存在未应用迁移，执行迁移即可完成升级。", "python manage.py migrate"),
    "schema-drift": (
        "迁移记录完整但库结构缺表/缺列（疑似旧版同名迁移的漂移库），不要直接 migrate。",
        "按 docs/ops/upgrade-stock.md 排查：核对版本来源，必要时备份后清库重建。",
    ),
    "legacy-chain": (
        "存在不在当前代码链中的迁移记录（该库来自旧版链路），不支持原地升级。",
        "按 docs/ops/upgrade-stock.md 走「备份 + 清库重建 + 数据回灌」；不要直接 migrate。",
    ),
}


def _migration_ledger(connection) -> dict:
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(connection, ignore_no_migrations=True)
    applied = set((loader.applied_migrations or {}).keys())
    replaced = set(loader.replacements.keys())
    expected = {key for key in loader.graph.nodes if key not in replaced}
    return {
        "applied": len(applied),
        "pending": sorted(f"{app}.{name}" for app, name in expected - applied),
        "orphan": sorted(f"{app}.{name}" for app, name in applied - expected),
    }


def _schema_ledger(connection) -> dict:
    from django.apps import apps

    models = [model for model in apps.get_models() if getattr(model._meta, "managed", True)]
    tables: dict[str, object] = {}
    for model in models:
        tables.setdefault(model._meta.db_table, model)
    with connection.cursor() as cursor:
        existing = set(connection.introspection.table_names(cursor))
    missing_tables = sorted(name for name in tables if name not in existing)
    missing_columns = []
    for name, model in tables.items():
        if name not in existing:
            continue
        with connection.cursor() as cursor:
            description = connection.introspection.get_table_description(cursor, name)
        columns = {str(getattr(column, "name", "")).lower() for column in description}
        expected_columns = {
            str(field.column).lower()
            for field in model._meta.local_fields  # type: ignore[attr-defined]
            if getattr(field, "concrete", False)
            and getattr(field, "column", None)
            # 向量列随 pgvector 扩展可选：扩展缺失的库列不存在属设计内降级
            # （检索层回退词频通道），不参与漂移判定
            and field.__class__.__name__ != "VectorField"
        }
        absent = sorted(expected_columns - columns)
        if absent:
            missing_columns.append({"table": name, "columns": absent})
    return {
        "tables_checked": len(tables),
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
    }


def collect_report(connection) -> dict:
    """采集体检报告（只读，不写任何数据）。"""
    return {"migrations": _migration_ledger(connection), "schema": _schema_ledger(connection)}


def evaluate(report: dict) -> tuple[str, int]:
    """按报告判定升级路径与退出码（纯函数，便于单测覆盖各分支）。"""
    migrations, schema = report["migrations"], report["schema"]
    if migrations["orphan"]:
        return "legacy-chain", 1
    if migrations["pending"]:
        return "needs-migrate", 0
    if schema["missing_tables"] or schema["missing_columns"]:
        return "schema-drift", 1
    return "up-to-date", 0


class Command(BaseCommand):
    help = "Pre-upgrade check (read-only): migration ledger vs current schema and upgrade verdict"

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json", help="JSON 输出（供脚本/安装器消费）")

    def handle(self, *args, **options):
        from django.db import connection

        report = collect_report(connection)
        verdict, exit_code = evaluate(report)
        report["verdict"] = verdict
        report["exit_code"] = exit_code
        if options["as_json"]:
            self.stdout.write(json.dumps(report, ensure_ascii=False))
        else:
            self._render(report)
        if exit_code:
            raise SystemExit(exit_code)

    def _render(self, report):
        migrations, schema = report["migrations"], report["schema"]
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("[xadmin upgrade-check] 升级前体检（只读）"))
        self.stdout.write("-" * 64)
        self.stdout.write(
            f"迁移记录：已应用 {migrations['applied']}，待应用 {len(migrations['pending'])}，"
            f"游离记录 {len(migrations['orphan'])}"
        )
        self._detail("待应用", migrations["pending"])
        self._detail("游离记录（不在当前代码链）", migrations["orphan"])
        self.stdout.write(
            f"表结构：检查 {schema['tables_checked']} 张表，缺失 {len(schema['missing_tables'])}，"
            f"缺列 {len(schema['missing_columns'])}"
        )
        self._detail("缺失表", schema["missing_tables"])
        if schema["missing_columns"]:
            lines = [f"{item['table']}: {', '.join(item['columns'])}" for item in schema["missing_columns"]]
            self._detail("缺列表", lines)
        summary, fix = VERDICTS[report["verdict"]]
        style = self.style.SUCCESS if report["exit_code"] == 0 else self.style.ERROR
        self.stdout.write(style(f"结论：{report['verdict']} —— {summary}"))
        if fix:
            self.stdout.write(f"       ↳ 处置：{fix}")

    def _detail(self, label, items):
        for item in items[:DETAIL_LIMIT]:
            self.stdout.write(f"        · {label}：{item}")
        if len(items) > DETAIL_LIMIT:
            self.stdout.write(f"        · ……其余 {len(items) - DETAIL_LIMIT} 条略")
