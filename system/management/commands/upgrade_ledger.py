#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""迁移账本认领命令（upgrade_ledger）：4.x 迁移链 → 5.0 合并链的对账与认领。

5.0 把各业务 app 的分散迁移合并为初始迁移（表结构等价，见
``system/utils/platform/migration_ledger.py``）。存量库直接 ``migrate`` 会因
「游离记录 + 合并链待应用」而状态错乱，本命令按对账结果完成认领：

- ``consistent``：迁移记录与当前链一致（全新库 / 已认领）——no-op；
- ``reconcilable``：库来自 4.2.5 链且表结构一致——删除游离记录并补记合并链迁移
  （**不执行 DDL**）；``--apply`` 才会写入，默认只报告；
- ``unsupported``：存在未知链路游离记录或表结构漂移——拒绝写入并退出码 1。

用法：
    python manage.py upgrade_ledger           # 只读对账（默认）
    python manage.py upgrade_ledger --apply   # 执行认领（幂等）
    python manage.py upgrade_ledger --json    # JSON 输出（供安装器 / 脚本消费）
"""

import json
from typing import Any

from django.core.management.base import BaseCommand

from system.utils.platform.migration_ledger import (
    VERDICTS,
    LedgerError,
    collect_ledger,
    evaluate_ledger,
    reconcile,
)


class Command(BaseCommand):
    help = "Reconcile migration ledger between the 4.x chain and the consolidated 5.x chain"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--apply", action="store_true", help="执行认领（默认只读报告）")
        parser.add_argument("--json", action="store_true", dest="as_json", help="JSON 输出")

    def handle(self, *args: Any, **options: Any) -> None:
        from django.db import connection

        ledger = collect_ledger(connection)
        verdict = evaluate_ledger(ledger)
        result: dict[str, Any] = {"verdict": verdict, "removed": [], "recorded": []}
        if options["apply"] and verdict != "unsupported":
            try:
                result = reconcile(connection)
            except LedgerError as exc:
                self.stderr.write(self.style.ERROR(str(exc)))
                raise SystemExit(1) from exc

        payload = {**ledger, **result, "applied_changes": bool(options["apply"])}
        if options["as_json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False))
        else:
            self._render(payload)

        if verdict == "unsupported":
            raise SystemExit(1)

    def _render(self, payload: dict[str, Any]) -> None:
        migrations = payload["migrations"]
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("[xadmin upgrade-ledger] 迁移账本对账"))
        self.stdout.write("-" * 64)
        self.stdout.write(
            f"迁移记录：已应用 {migrations['applied']}，待应用 {len(migrations['pending'])}，"
            f"4.x 游离 {len(migrations['legacy_orphan'])}，未知游离 {len(migrations['unknown_orphan'])}"
        )
        self._detail("待应用", migrations["pending"])
        self._detail("4.x 游离（可认领）", migrations["legacy_orphan"])
        self._detail("未知游离（需人工介入）", migrations["unknown_orphan"])
        if payload["removed"]:
            self._detail("已删除的游离记录", payload["removed"])
        if payload["recorded"]:
            self._detail("已补记的合并链迁移", payload["recorded"])
        style = self.style.ERROR if payload["verdict"] == "unsupported" else self.style.SUCCESS
        self.stdout.write(style(f"结论：{payload['verdict']} —— {VERDICTS[payload['verdict']]}"))
        if payload["verdict"] == "reconcilable" and not payload["applied_changes"]:
            self.stdout.write("       ↳ 处置：python manage.py upgrade_ledger --apply")

    def _detail(self, label: str, items: list[str]) -> None:
        for item in items[:10]:
            self.stdout.write(f"        · {label}：{item}")
        if len(items) > 10:
            self.stdout.write(f"        · ……其余 {len(items) - 10} 条略")
