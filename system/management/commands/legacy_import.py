#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""把 ``legacy_export`` 的产物回灌进新库（四域切分之后的结构）。

前置：新库已完成初始化（``migrate`` + ``ops/init_data.py`` 或 ``load_init_json``），
菜单与权限点由种子提供，本命令只搬运业务数据（组织、角色、用户、数据权限、系统配置、
个人配置、附件、公告、消息订阅、字段权限），关联按自然键解析（``code`` / ``username`` / ``name``），
旧库的 UUID 不保留。

幂等：按自然键 upsert（重复执行只更新不重复建）；引用缺失（如角色绑定的菜单在当前种子中
不存在）会计数跳过并汇总告警，不中断导入；单实体失败只跳过该实体。

用法::

    python manage.py legacy_import --input /tmp/xadmin-legacy
    python manage.py legacy_import --input /tmp/xadmin-legacy --dry-run
"""

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from ops.legacy import transfer as lt
from ops.legacy.importer import ORDER, Importer


class Command(BaseCommand):
    help = "Import legacy business data (exported by legacy_export) into the current database"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--input", required=True, help="legacy_export 的输出目录")
        parser.add_argument("--include", default=None, help="只导入指定实体，逗号分隔")
        parser.add_argument("--dry-run", action="store_true", help="只校验与统计，不写库")

    def handle(self, *args: Any, **options: Any) -> None:
        source = Path(options["input"]).expanduser()
        manifest = source / "export.json"
        if not manifest.exists():
            raise CommandError(f"未找到导出文件：{manifest}")
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        lt.validate_payload(payload)
        try:
            names = lt.parse_include(options["include"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        importer = Importer(source, dry_run=options["dry_run"])
        self.stdout.write(f"导出包：{manifest}（源版本 {payload.get('source_version')}）")
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("dry-run：只统计，不写库"))
        for name in [item for item in ORDER if item in names]:
            rows = payload["data"].get(name) or []
            # 单实体失败不中断整体：否则后续实体与收尾（如部门父子链接）都不会执行
            try:
                created, updated, skipped = importer.import_entity(name, rows)
            except Exception as exc:  # noqa: BLE001 - 逐实体隔离，最终统一汇总
                importer.record_error(f"{name}: {exc}")
                self.stderr.write(self.style.ERROR(f"  · {name}: 导入失败（已跳过该实体）：{exc}"))
                continue
            self.stdout.write(f"  · {name}: 新建 {created}，更新 {updated}，跳过 {skipped}")

        importer.finish()
        extra = payload.get("extra_tables") or {}
        if extra:
            for table, created, skipped in importer.import_extra_tables(extra):
                self.stdout.write(f"  · [二开表] {table}: 新建 {created}，跳过 {skipped}")
        reset = importer.reset_sequences()
        if reset:
            self.stdout.write(f"  · 已重置 {reset} 张表的自增序列（保留原 ID 的必要收尾）")
        if importer.encrypted_settings:
            encrypted_names = ", ".join(importer.encrypted_settings[:10])
            self.stdout.write(
                self.style.WARNING(
                    f"加密设置 {len(importer.encrypted_settings)} 项未搬运取值：{encrypted_names}"
                    f"{' …' if len(importer.encrypted_settings) > 10 else ''}"
                )
            )
            self.stdout.write("  密文由旧库 SECRET_KEY 签发，跨库无法解密；请在新库设置页重新填写。")
        for message in importer.errors:
            self.stderr.write(self.style.ERROR(f"  ! {message}"))
        if importer.skipped_refs:
            self.stdout.write(self.style.WARNING(f"引用缺失 {sum(importer.skipped_refs.values())} 处："))
            for ref, count in sorted(importer.skipped_refs.items()):
                self.stdout.write(f"    - {ref}: {count}")
            self.stdout.write("  多为菜单/权限点未灌种子或数据本身已被删除；确认后可忽略。")
        self.stdout.write(self.style.SUCCESS("\n回灌完成；请执行 upgrade_check 复核，并重启后端容器。"))
        if importer.skipped_refs and not options["dry_run"]:
            self.stdout.write(self.style.WARNING("存在引用缺失，建议逐项核对上面的清单。"))
