#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""存量库结构差异报告（真·原地升级的判定入口，只读）。

把目标库（通常是待升级的旧库，或由 ``ops/legacy_sample_db.py`` 生成的旧结构样本）
与当前代码的目标结构做表级 / 列级比对，输出：改名表、由 migrate 新建的表、补列 / 类型变更、
以及**阻断项**（无法唯一推断映射的旧表）。

退出码：``0`` = 结构可自动对齐（无阻断项，仍可能有需人工复核的类型变更）；``1`` = 存在阻断项。
本命令**不写入任何数据**；执行由后续阶段的原地对齐器承担。

用法::

    python manage.py legacy_schema_report --dsn "postgresql://user:pwd@host:5432/xadmin"
    python manage.py legacy_schema_report --dsn "..." --tag v4.2.5 --json
"""

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from system.utils.platform import legacy_schema


class Command(BaseCommand):
    help = "Report schema differences between a legacy database and the current target structure"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--dsn", required=True, help="待比对库的连接串（只读）")
        parser.add_argument("--tag", default=None, help="历史 tag（如 v4.2.5）：用于把旧表映射到历史模型")
        parser.add_argument("--json", action="store_true", help="输出 JSON（供脚本消费）")
        parser.add_argument("--plan", action="store_true", help="附带对齐计划（dry-run 步骤清单）")
        parser.add_argument("--limit", type=int, default=25, help="人类可读输出里每节最多列出的条目数")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            report = legacy_schema.collect_report(options["dsn"], tag=options["tag"])
        except Exception as exc:  # noqa: BLE001 - 连接失败属输入问题，转成明确报错
            raise CommandError(f"结构比对失败：{exc}") from exc

        if options["json"]:
            payload = report if options["plan"] else {key: value for key, value in report.items() if key != "plan"}
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            self._write_human(report, limit=options["limit"])
        if report["blockers"]:
            raise SystemExit(1)

    def _write_human(self, report: dict[str, Any], limit: int) -> None:
        self.stdout.write(
            f"结构目录：旧库 {report['tables_old']} 张表 ↔ 目标 {report['tables_new']} 张表（框架基础设施表不参与）"
        )
        self.stdout.write(f"\n【表改名】{len(report['renamed'])} 张（PostgreSQL 改名保留数据与约束）")
        for source, target in report["renamed"][:limit]:
            self.stdout.write(f"  - {source} → {target}")
        if len(report["renamed"]) > limit:
            self.stdout.write(f"  …… 其余 {len(report['renamed']) - limit} 张见 --json")

        changes = [item for item in report["changes"] if not item["renamed"]] or report["changes"]
        self.stdout.write(f"\n【列变更】{len(changes)} 张表")
        for change in changes[:limit]:
            head = f"  - {change['new']}"
            if change["renamed"]:
                head += f"（原名 {change['old']}）"
            self.stdout.write(head)
            for label, key in (
                ("补列", "columns_added"),
                ("类型变更（需复核）", "columns_type_changed"),
                ("旧列多余（需复核）", "columns_removed"),
            ):
                if change[key]:
                    self.stdout.write(f"      {label}：{', '.join(change[key])}")
        if len(changes) > limit:
            self.stdout.write(f"  …… 其余 {len(changes) - limit} 张表见 --json")

        self.stdout.write(f"\n【由 migrate 新建】{len(report['created_by_migrate'])} 张表（旧库没有、升级时建表即可）")
        if report["blockers"]:
            self.stdout.write(f"\n【阻断项】{len(report['blockers'])} 条（无法自动对齐，需人工指定映射）")
            for item in report["blockers"]:
                self.stdout.write(f"  ! {item}")
            self.stdout.write("\n结论：存在阻断项，不能自动对齐；请在映射目录登记后重跑。")
        else:
            self.stdout.write("\n结论：结构可自动对齐（类型变更与旧列多余的条目仍建议人工复核）。")
