#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""升级前体检（upgrade_check）：迁移记录对账 + 表结构体检 + 升级路径判定（只读）。

迁移链经过合并后，旧版部署的 ``django_migrations`` 记录可能与当前代码链不兼容
（记录名不存在于代码中），或同名迁移的内容已变化导致库结构漂移——直接执行
``migrate`` 会造成状态错乱。本命令在升级前给出判定与处置建议：

- ``up-to-date``：迁移记录与表结构均与当前代码一致；
- ``needs-migrate``：存在未应用迁移（含全新空库），执行 ``manage.py migrate`` 即可；
- ``legacy-chain-reconcilable``：库来自 4.2.5 迁移链且表结构一致（5.0 合并链的账本
  未认领）——先执行 ``manage.py upgrade_ledger --apply`` 认领账本（不执行 DDL），
  再 ``migrate``；
- ``schema-drift``：迁移记录完整但表/列缺失（同名迁移内容已变化的漂移库）——人工排查；
- ``legacy-chain``：存在不在当前代码链中的迁移记录（更早版本的库）——不支持原地升级，
  按 ``docs/ops/upgrade-stock.md`` 走「备份 + 清库重建 + 数据回灌」。

退出码：0 = 可继续升级（up-to-date / needs-migrate / legacy-chain-reconcilable）；
1 = 需人工介入。
"""

import json
from typing import Any

from django.core.management.base import BaseCommand

from system.utils.platform.migration_ledger import collect_migration_ledger, collect_schema_ledger

DETAIL_LIMIT = 10

VERDICTS = {
    "up-to-date": ("库已与当前代码一致，无需迁移。", None),
    "needs-migrate": ("存在未应用迁移，执行迁移即可完成升级。", "python manage.py migrate"),
    "legacy-chain-reconcilable": (
        "库来自 4.2.5 迁移链且表结构一致，迁移账本待认领。",
        "python manage.py upgrade_ledger --apply && python manage.py migrate",
    ),
    "schema-drift": (
        "迁移记录完整但库结构缺表/缺列（疑似旧版同名迁移的漂移库），不要直接 migrate。",
        "按 docs/ops/upgrade-stock.md 排查：核对版本来源，必要时备份后清库重建。",
    ),
    "legacy-chain": (
        "存在不在当前代码链中的迁移记录（该库来自旧版链路），不支持原地升级。",
        "按 docs/ops/upgrade-stock.md 走「备份 + 清库重建 + 数据回灌」；不要直接 migrate。",
    ),
}


def collect_report(connection: Any) -> dict[str, Any]:
    """采集体检报告（只读，不写任何数据）。"""
    return {"migrations": collect_migration_ledger(connection), "schema": collect_schema_ledger(connection)}


def evaluate(report: dict[str, Any]) -> tuple[str, int]:
    """按报告判定升级路径与退出码（纯函数，便于单测覆盖各分支）。"""
    migrations, schema = report["migrations"], report["schema"]
    unknown_orphan = migrations.get("unknown_orphan", migrations.get("orphan", []))
    legacy_orphan = migrations.get("legacy_orphan", [])
    if unknown_orphan:
        return "legacy-chain", 1
    # 表结构体检只在「已有迁移记录」时有意义：全新空库（首次安装）缺表是 migrate 的正常
    # 前态（本模块 docstring 也把全新空库归入 needs-migrate），不做此判据会把首次安装
    # 误判为 schema-drift 并阻断（安装器 preflight 默认中止）
    if migrations["applied"] and (schema["missing_tables"] or schema["missing_columns"]):
        return "schema-drift", 1
    if legacy_orphan:
        return "legacy-chain-reconcilable", 0
    if migrations["pending"]:
        return "needs-migrate", 0
    return "up-to-date", 0


class Command(BaseCommand):
    help = "Pre-upgrade check (read-only): migration ledger vs current schema and upgrade verdict"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--json", action="store_true", dest="as_json", help="JSON 输出（供脚本/安装器消费）")

    def handle(self, *args: Any, **options: Any) -> None:
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

    def _render(self, report: Any) -> None:
        migrations, schema = report["migrations"], report["schema"]
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("[xadmin upgrade-check] 升级前体检（只读）"))
        self.stdout.write("-" * 64)
        legacy_orphan = migrations.get("legacy_orphan", [])
        unknown_orphan = migrations.get("unknown_orphan", migrations.get("orphan", []))
        self.stdout.write(
            f"迁移记录：已应用 {migrations['applied']}，待应用 {len(migrations['pending'])}，"
            f"4.x 游离 {len(legacy_orphan)}，未知游离 {len(unknown_orphan)}"
        )
        self._detail("待应用", migrations["pending"])
        self._detail("4.x 游离记录（可认领）", legacy_orphan)
        self._detail("未知游离记录（不在当前代码链）", unknown_orphan)
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

    def _detail(self, label: Any, items: Any) -> None:
        for item in items[:DETAIL_LIMIT]:
            self.stdout.write(f"        · {label}：{item}")
        if len(items) > DETAIL_LIMIT:
            self.stdout.write(f"        · ……其余 {len(items) - DETAIL_LIMIT} 条略")
