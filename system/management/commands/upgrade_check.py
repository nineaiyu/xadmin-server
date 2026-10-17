#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""升级前体检（upgrade_check）：迁移记录对账 + 表结构体检 + 升级路径判定（只读）。

迁移链经过合并后，旧版部署的 ``django_migrations`` 记录可能与当前代码链不兼容
（记录名不存在于代码中），或同名迁移的内容已变化导致库结构漂移——直接执行
``migrate`` 会造成状态错乱。本命令在升级前给出判定与处置建议：

- ``up-to-date``：迁移记录与表结构均与当前代码一致；
- ``needs-migrate``：存在未应用迁移（含全新空库），执行 ``manage.py migrate`` 即可；
- ``legacy-chain-reconcilable``：库来自**四域切分之后**的 4.x 链且表结构一致（5.0 合并链
  的账本未认领）——先执行 ``manage.py upgrade_ledger --apply`` 认领账本（不执行 DDL），
  再 ``migrate``；
- ``schema-drift``：迁移记录完整但表/列缺失（同名迁移内容已变化的漂移库）——人工排查；
- ``legacy-chain-pre-split``：库结构停留在**四域切分之前**（``system_userinfo`` 等旧表名，
  字段集也更少）——表名与列都与合并链不一致，认领无法收敛，走「备份 + 清库重建 +
  ``legacy_export`` / ``legacy_import`` 回灌」；
- ``legacy-chain``：存在不在当前代码链中的迁移记录（其他旧版链路）——不支持原地升级，
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
        "库来自四域切分后的 4.x 迁移链且表结构一致，迁移账本待认领。",
        "python manage.py upgrade_ledger --apply && python manage.py migrate",
    ),
    "legacy-inplace-capable": (
        "库结构停留在四域切分之前，但结构差异可以自动对齐：原地升级（表改名 / 补列 / 补表 / 引用与账本重写）"
        "可保留数据、主键与二开表，不需要清库重建。",
        '先备份，再执行 python manage.py legacy_align --dsn "<该库连接串>" --tag <旧版本 tag> '
        "（先不加 --apply 看预演）→ 加 --apply 执行 → 复检 python manage.py upgrade_check；"
        "局限与人工复核项见 docs/plans/原地升级立项与方案-2026.10.md。",
    ),
    "legacy-chain-pre-split": (
        "库结构停留在四域切分之前（用户/文件/日志仍在 system_* 表名下），且结构差异**无法自动对齐**"
        "（存在未登记映射或缺失列）；账本认领无法收敛。",
        "按上面的阻断项逐条处理；若不便处理，按 docs/ops/upgrade-stock.md 走「备份 + 清库重建 + 数据回灌」"
        "（旧库 python manage.py legacy_export，新库 python manage.py legacy_import）；不要直接 migrate。",
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


def collect_report(connection: Any, dsn: str | None = None, tag: str | None = None) -> dict[str, Any]:
    """采集体检报告（只读，不写任何数据）。

    结构停留在四域切分之前时，额外跑一次**只读的原地升级预演**（``legacy_align.build_steps``）：
    给出「可原地升级 / 阻断原因 / 改名与补列规模 / 二开表数量」，让判定从「一律拒绝」变成
    「能对齐的给路径、不能对齐的给阻断项」。
    """
    report = {"migrations": collect_migration_ledger(connection), "schema": collect_schema_ledger(connection)}
    if report["schema"].get("pre_split_tables"):
        report["inplace"] = collect_inplace_report(connection, dsn=dsn, tag=tag)
    return report


def collect_inplace_report(connection: Any, dsn: str | None = None, tag: str | None = None) -> dict[str, Any]:
    """原地升级可行性（只读）：结构差异规模 + 阻断项 + 二开表清单。"""
    from system.utils.platform import legacy_align

    target = dsn or dsn_from_settings(connection)
    try:
        prepared = legacy_align.build_steps(target, tag=tag)
    except Exception as exc:  # noqa: BLE001 - 预演失败不阻断体检，如实报告
        return {"capable": False, "error": str(exc), "blockers": [f"预演失败：{exc}"]}
    diff = prepared["report"]
    return {
        "capable": not diff["blockers"],
        "tag": tag or "",
        "renames": len(diff["renamed"]),
        "planned_columns": len([item for item in prepared["steps"] if item["step"] == "add_column"]),
        "tables_new": len(diff["created_by_migrate"]),
        "third_party_tables": diff["kept_unknown"],
        "review": {
            "type_changes": sum(len(item["columns_type_changed"]) for item in diff["changes"]),
            "extra_columns": sum(len(item["columns_removed"]) for item in diff["changes"]),
        },
        "blockers": diff["blockers"],
        "notes": diff.get("notes", []),
        "skipped": prepared["skipped"],
    }


def dsn_from_settings(connection: Any) -> str:
    """由连接配置拼出 DSN（预演走 psycopg / 结构引擎，需要连接串）。"""
    from urllib.parse import quote

    settings_dict = connection.settings_dict
    return (
        f"postgresql://{quote(settings_dict['USER'])}:{quote(settings_dict.get('PASSWORD') or '')}"
        f"@{settings_dict['HOST']}:{settings_dict['PORT']}/{settings_dict['NAME']}"
    )


def evaluate(report: dict[str, Any]) -> tuple[str, int]:
    """按报告判定升级路径与退出码（纯函数，便于单测覆盖各分支）。"""
    migrations, schema = report["migrations"], report["schema"]
    unknown_orphan = migrations.get("unknown_orphan", migrations.get("orphan", []))
    legacy_orphan = migrations.get("legacy_orphan", [])
    # 四域切分前的旧库优先判定：它的表名与列集都与合并链不同，与「游离记录是否可认领」
    # 无关，必须先于未知游离判定给出结论（否则会被笼统报成 legacy-chain，误导用户以为
    # 版本号够就能原地升级）。
    if schema.get("pre_split_tables"):
        inplace = report.get("inplace") or {}
        # 没有预演信息（纯函数单测 / 预演不可用）时保持保守：仍按「需人工介入」返回
        if inplace.get("capable"):
            return "legacy-inplace-capable", 0
        return "legacy-chain-pre-split", 1
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
        parser.add_argument("--dsn", default=None, help="待体检库的连接串（默认当前连接；供预检另一个库）")
        parser.add_argument("--tag", default=None, help="历史 tag（如 v4.2.5）：提高原地升级预演的表改名推断率")

    def handle(self, *args: Any, **options: Any) -> None:
        from django.db import connection

        from system.utils.platform import legacy_structure

        target = connection
        if options["dsn"]:
            # 体检另一个库（预检、演练）：整套对账都切到该库的连接上
            target = legacy_structure.connect_legacy(options["dsn"])
        try:
            report = collect_report(target, dsn=options["dsn"], tag=options["tag"])
        finally:
            if options["dsn"]:
                legacy_structure.close_legacy(target)
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
        pre_split = schema.get("pre_split_tables") or []
        if pre_split:
            self._detail(
                "四域切分前的旧表（表名未迁移）",
                [f"{item['legacy']} → 现应为 {item['current']}" for item in pre_split],
            )
        inplace = report.get("inplace")
        if inplace:
            self.stdout.write(
                f"原地升级预演：可对齐 {'是' if inplace.get('capable') else '否'}，"
                f"改名 {inplace.get('renames', 0)} 张，补列 {inplace.get('planned_columns', 0)} 列，"
                f"新建 {inplace.get('tables_new', 0)} 张，二开表 {len(inplace.get('third_party_tables', []))} 张"
                f"（原样保留）"
            )
            review = inplace.get("review") or {}
            self.stdout.write(
                f"        · 需人工复核：类型变更 {review.get('type_changes', 0)} 处、"
                f"旧列多余 {review.get('extra_columns', 0)} 处"
            )
            for note in inplace.get("notes", []):
                self.stdout.write(f"        · 注意：{note}")
            self._detail("阻断项（需登记映射后重跑）", inplace.get("blockers", []))
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
