#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""存量库原地升级（结构对齐 + 引用层 + 迁移账本；默认 dry-run，``--apply`` 才执行）。

一次执行覆盖四件事，顺序固定：

1. **结构**：表改名（``ALTER TABLE RENAME``，PG 保留数据、索引、序列与外键）→ 补列 →
   缺失表（Django ``CreateModel``；含 0002 的外键列、模型级索引与约束、自动 M2M 中间表）
   → 前置扩展（pgvector / pg_trgm，白名单内）；
2. **引用层**：``django_content_type`` 的 ``app_label`` 重指（模型换域后权限点仍挂在同一行）
   + 存量「模型标签」字符串重指（字段树、数据权限规则等，登记表驱动）；
3. **迁移账本**：删旧链游离记录、把当前链补记为已应用（此时结构与当前版本一致）；
4. **复检**：残留改名 / 阻断项 / 缺失表 / 列差异逐项报告。

边界：NOT NULL 且默认值不可写成字面量的列、目标端已删除的旧列、列类型变更都**只报告不执行**
（见输出末尾的残留差异）；可调用默认值列的落地策略与二开 app 的迁移图适配属后续批次。

用法::

    python manage.py legacy_align --dsn "postgresql://..." --tag v4.2.5            # dry-run
    python manage.py legacy_align --dsn "postgresql://..." --tag v4.2.5 --apply
"""

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from system.utils.platform import legacy_align as engine


class Command(BaseCommand):
    help = "Upgrade a legacy database in place (structure + references + migration ledger)"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--dsn", default=None, help="旧库连接串（默认当前连接；安装器场景即容器内默认库）")
        parser.add_argument("--tag", default=None, help="历史 tag（如 v4.2.5）：用于表改名与标签映射推断")
        parser.add_argument("--apply", action="store_true", help="执行（默认只出计划）")
        parser.add_argument(
            "--probe",
            action="store_true",
            help="只探询是否需要原地对齐：一行结论 + 退出码 0=需要 / 1=已一致 / 2=阻断 / 3=无法判定",
        )
        parser.add_argument("--no-references", action="store_true", help="跳过存量字符串重指")
        parser.add_argument("--no-ledger", action="store_true", help="跳过迁移账本重写（只对齐结构）")
        parser.add_argument("--json", action="store_true", help="输出 JSON（供脚本消费）")
        parser.add_argument("--limit", type=int, default=20, help="人类可读输出里每节最多列出的条目数")

    def handle(self, *args: Any, **options: Any) -> None:
        dsn = options["dsn"] or self._default_dsn()
        if options["probe"]:
            raise SystemExit(self._probe(dsn, options["tag"]))
        try:
            result = engine.align_full(
                dsn,
                tag=options["tag"],
                dry_run=not options["apply"],
                with_references=not options["no_references"],
                with_ledger=not options["no_ledger"],
            )
        except Exception as exc:  # noqa: BLE001 - 输入/连接问题转明确报错
            raise CommandError(f"原地升级失败：{exc}") from exc

        if options["json"]:
            self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            self._write_human(result, limit=options["limit"])
        if result.get("refused"):
            raise SystemExit(1)

    def _default_dsn(self) -> str:
        from django.db import connection

        from system.utils.platform import legacy_structure

        return legacy_structure.dsn_from_connection(connection)

    def _probe(self, dsn: str, tag: str | None) -> int:
        """探询结论（安装器消费）：0=需要对齐 / 1=结构已一致 / 2=有阻断项 / 3=无法判定。

        无法判定（连不上库、预演报错）与「阻断」分开：安装器对这两种情况的处置不同
        （前者继续走原有链路并告警，后者中止并要求人工处理）。
        """
        try:
            prepared = engine.build_steps(dsn, tag=tag)
        except Exception as exc:  # noqa: BLE001 - 探询失败不抛栈，交由安装器决定
            self.stdout.write(f"inplace-unknown（预演失败：{exc}）")
            return 3
        if prepared["report"]["blockers"]:
            self.stdout.write("inplace-blocked")
            for item in prepared["report"]["blockers"]:
                self.stdout.write(f"  ! {item}")
            return 2
        if not prepared["steps"]:
            self.stdout.write("inplace-none")
            return 1
        self.stdout.write(
            f"inplace-needed（改名 {len([i for i in prepared['steps'] if i['step'] == 'rename_table'])} 张，"
            f"补列 {len([i for i in prepared['steps'] if i['step'] == 'add_column'])} 列，"
            f"二开表 {len(prepared['report']['kept_unknown'])} 张原样保留）"
        )
        return 0

    def _write_human(self, result: dict[str, Any], limit: int) -> None:
        mode = "计划（dry-run）" if result["dry_run"] else "已执行"
        self.stdout.write(f"原地升级{mode}：结构步骤 {result['structure']['planned']} 项")
        if result.get("refused"):
            self.stdout.write(f"\n拒绝执行：{result['refused']}")
            for item in result["blockers"][:limit]:
                self.stdout.write(f"  ! {item}")
            return
        self._write_structure(result, limit)
        self._write_references(result, limit)
        self._write_after(result, limit)

    def _write_structure(self, result: dict[str, Any], limit: int) -> None:
        for note in result.get("notes", []):
            self.stdout.write(f"注意：{note}")
        structure = result["structure"]
        steps = structure.get("steps", [])
        renames = [item for item in steps if item["step"] == "rename_table"]
        if result["dry_run"]:
            self.stdout.write(f"  · 表改名 {len(renames)} 张 / 补列 {structure['planned'] - len(renames)} 列")
            self.stdout.write(
                f"  · 待建表 {len(result['missing_tables'])} 张，待补索引"
                f" {len(result.get('missing_indexes', []))} 个，待补约束"
                f" {len(result.get('missing_constraints', []))} 个"
            )
        else:
            self.stdout.write(
                f"  · 表改名 + 补列 {structure['executed']} 步；建表 {len(result['missing_tables'])} 张"
                f"（含中间表）；补列 {len(result.get('missing_fields', []))} 列；"
                f"补索引 {len(result.get('missing_indexes', []))} 个；补约束 {len(result.get('missing_constraints', []))} 个"
            )
            self.stdout.write(f"  · 前置扩展：{', '.join(result.get('prerequisites', [])) or '无'}")
        if structure["skipped"]:
            self.stdout.write(f"  · 跳过（未登记的操作，属人工复核项）{len(structure['skipped'])} 条")
            for item in structure["skipped"][:limit]:
                self.stdout.write(f"      {item}")

    def _write_references(self, result: dict[str, Any], limit: int) -> None:
        content_types = result.get("content_types") or {}
        references = result.get("references") or {}
        self.stdout.write("\n【引用层】")
        self.stdout.write(
            f"  · 内容类型重指：更新 {content_types.get('updated', 0)} 行"
            f"（标签迁移 {len(content_types.get('moves', {}))} 条，未命中 {len(content_types.get('missing', []))}，"
            f"遗留旧域 {len(content_types.get('stale', []))}）"
        )
        for item in content_types.get("missing", [])[:limit]:
            self.stdout.write(f"      未命中：{item}（库里没有该 content type，通常是旧库未启用该功能）")
        for target in references.get("targets", []):
            self.stdout.write(f"  · {target['target']}：改写 {target['changed']} 行")
        for item in references.get("skipped", [])[:limit]:
            self.stdout.write(f"      跳过：{item}")
        ledger = result.get("ledger") or {}
        if ledger.get("deleted") is None:
            self.stdout.write(f"  · 迁移账本：{ledger.get('note', '未处理')}")
        else:
            self.stdout.write(f"  · 迁移账本：删旧链 {ledger['deleted']} 条，补记当前链 {ledger['recorded']} 条")

    def _write_after(self, result: dict[str, Any], limit: int) -> None:
        after = result.get("after")
        if not after:
            self.stdout.write("\n未执行（dry-run）。确认计划后加 --apply。")
            return
        self.stdout.write(
            f"\n【执行后复检】残留改名 {after['renamed_left']}，阻断项 {len(after['blockers'])}，"
            f"缺失表 {len(after['missing_tables'])}，仍有列差异的表 {after['tables_with_changes']}"
        )
        for change in after["changes"][:limit]:
            detail = []
            for label, key in (
                ("补列", "columns_added"),
                ("类型", "columns_type_changed"),
                ("旧列", "columns_removed"),
            ):
                if change[key]:
                    detail.append(f"{label} {', '.join(change[key])}")
            self.stdout.write(f"      {change['new']}：{'；'.join(detail)}")
        self.stdout.write(
            "\n结论：结构与当前版本一致（表格/索引/约束/账本均已对齐；上面列出的差异属人工复核项）。"
            if result.get("aligned")
            else "\n结论：仍有未完成项，见上面的复检输出。"
        )
