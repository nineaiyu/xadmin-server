#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : log_archive
"""审计日志冷归档命令（P-5）。

用法示例::

    python manage.py log_archive                     # 归档全部「整月已超保留期」的月份（幂等）
    python manage.py log_archive --dry-run           # 只统计将归档的月份与行数
    python manage.py log_archive --month 2026-02     # 归档指定月份（可重复）
    python manage.py log_archive --model login --month 2026-01
    python manage.py log_archive --list              # 归档清单
    python manage.py log_archive --verify            # 校验完整性（sha256 + 行数）
    python manage.py log_archive --restore-range 2026-02 --grep xadmin --limit 50
    python manage.py log_archive --prune             # 水位驱动清理（删必已归档）
    python manage.py log_archive --prune --dry-run   # 报告水位与将删除行数
"""

from django.core.management.base import BaseCommand

from system.utils import log_archive

_TABLE_FIELDS = {
    "operation": ("created_time", "module", "method", "path", "status_code", "object_pk", "creator_id"),
    "login": ("created_time", "login_type", "status", "ipaddress", "city", "creator_id"),
}


class Command(BaseCommand):
    help = "审计日志冷归档：归档 / 校验 / 离线查询 / 水位驱动清理（P-5）"

    def add_arguments(self, parser):
        parser.add_argument("--model", choices=log_archive.ARCHIVE_MODEL_KEYS, default="operation", help="归档对象")
        parser.add_argument(
            "--month", action="append", default=None, help="归档指定月份 YYYY-MM（可重复；缺省 = 全部超期月份）"
        )
        parser.add_argument("--dry-run", action="store_true", help="只统计不写文件 / 不删除")
        parser.add_argument("--list", action="store_true", dest="list_archives", help="列出归档清单")
        parser.add_argument("--verify", action="store_true", help="校验归档完整性（配合 --month；缺省校验全部）")
        parser.add_argument("--restore-range", default=None, metavar="YYYY-MM", help="离线查询归档（不落库）")
        parser.add_argument("--grep", default=None, help="离线查询的原始行子串过滤")
        parser.add_argument("--limit", type=int, default=200, help="离线查询条数上限（0 = 不限）")
        parser.add_argument("--format", choices=("jsonl", "table"), default="jsonl", help="离线查询输出格式")
        parser.add_argument("--prune", action="store_true", help="执行水位驱动清理（删必已归档）")
        parser.add_argument("--dir", default=None, help="归档目录（缺省 settings.LOG_ARCHIVE_DIR）")

    def handle(self, *args, **options):
        directory = options["dir"]
        if options["restore_range"]:
            return self._restore(options, directory)
        if options["verify"]:
            return self._verify(options, directory)
        if options["list_archives"]:
            return self._list(directory)
        if options["prune"]:
            return self._prune(options, directory)
        return self._archive(options, directory)

    def _archive(self, options, directory):
        model_key = options["model"]
        months = options["month"]
        if months:
            for month in months:
                log_archive.parse_month(month)  # 提前校验格式
                manifest = log_archive.archive_month(model_key, month, directory=directory, dry_run=options["dry_run"])
                self._report_manifest(manifest)
            return
        result = log_archive.archive_expired(model_key, directory=directory, dry_run=options["dry_run"])
        if result.get("reason"):
            self.stdout.write(self.style.WARNING(f"无需归档：{result['reason']}"))
            return
        self.stdout.write(
            f"模型 {model_key}：新归档 {len(result['archived'])} 个月，已存在跳过 {len(result['skipped'])} 个月"
        )
        for manifest in result["archived"]:
            self._report_manifest(manifest)

    def _report_manifest(self, manifest):
        prefix = "将归档" if manifest.get("dry_run") else ("已存在" if manifest.get("skipped") else "已归档")
        self.stdout.write(
            f"[{prefix}] {manifest.get('model')} {manifest.get('month')}："
            f"{manifest.get('rows')} 行"
            + (
                f"，{manifest.get('bytes')} 字节，sha256={manifest.get('sha256', '')[:12]}"
                if manifest.get("sha256")
                else ""
            )
        )

    def _list(self, directory):
        manifests = log_archive.list_archives(directory)
        if not manifests:
            self.stdout.write("（暂无归档）")
            return
        for item in manifests:
            self.stdout.write(
                f"{item.get('model'):<10} {item.get('month')}  {item.get('rows'):>8} 行  "
                f"{item.get('bytes', 0):>10} 字节  {item.get('created_time', '')}"
            )

    def _verify(self, options, directory):
        model_key = options["model"]
        months = options["month"] or sorted(log_archive.archived_months(model_key, directory))
        if not months:
            self.stdout.write(self.style.WARNING("（暂无可校验的归档）"))
            return
        failed = 0
        for month in months:
            result = log_archive.verify_archive(model_key, month, directory=directory)
            if result["ok"]:
                self.stdout.write(f"[OK] {model_key} {month}：{result['rows']} 行，sha256 一致")
            else:
                failed += 1
                self.stderr.write(self.style.ERROR(f"[FAIL] {model_key} {month}：{result}"))
        if failed:
            raise SystemExit(1)

    def _restore(self, options, directory):
        model_key = options["model"]
        month = options["restore_range"]
        limit = options["limit"] or None
        fields = _TABLE_FIELDS.get(model_key, ())
        if options["format"] == "table":
            self.stdout.write(" | ".join(fields))
        emitted = 0
        for row in log_archive.read_restore_rows(
            model_key, month, directory=directory, grep=options["grep"], limit=limit
        ):
            if options["format"] == "table":
                self.stdout.write(" | ".join(str(row.get(field, "")) for field in fields))
            else:
                import json

                self.stdout.write(json.dumps(row, ensure_ascii=False))
            emitted += 1
        self.stderr.write(f"共输出 {emitted} 行（归档 {model_key} {month}）")

    def _prune(self, options, directory):
        model_key = options["model"]
        if log_archive.retention_days(model_key) <= 0:
            self.stdout.write(self.style.WARNING(f"{model_key} 日志保留期未启用（0 = 不清理），仅支持手动归档"))
            return
        if options["dry_run"]:
            watermark = log_archive.archive_watermark(model_key, directory=directory)
            if watermark is None:
                self.stdout.write(self.style.WARNING("无可清理边界（归档水位为空：请先执行归档）"))
                return
            model = log_archive.model_for(model_key)
            candidates = model.objects.filter(created_time__lt=watermark).count()
            self.stdout.write(
                f"归档水位 {log_archive.month_of(watermark)}（早于该月的已归档数据可清理）："
                f"将删除 {candidates} 行（dry-run 未执行）"
            )
            return
        deleted = log_archive.prune_archived(model_key, directory=directory)
        self.stdout.write(self.style.SUCCESS(f"清理完成：删除 {deleted} 行"))
