#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : storage_migrate
"""文件存储搬迁命令。

用法示例::

    python manage.py storage_migrate --dry-run             # 统计待搬迁（本地 → 对象存储）
    python manage.py storage_migrate                       # 执行搬迁（幂等，可中断后续跑）
    python manage.py storage_migrate --verify              # 校验目标完整性（存在性 + 大小）
    python manage.py storage_migrate --verify --md5        # 追加 md5 比对（慢）
    python manage.py storage_migrate --direction pull      # 对象存储 → 本地（回迁 / 撤离）
    python manage.py storage_migrate --limit 100           # 只处理前 100 个（分批观察）

前置：``FILE_STORAGE_BACKEND=s3`` 且配置齐全，并安装可选依赖
``pip install django-storages boto3``。
"""

from django.core.management.base import BaseCommand

from system.utils import storage_migrate as util


class Command(BaseCommand):
    help = "文件存储搬迁 / 校验：本地 <-> 对象存储，幂等可断点续搬"

    def add_arguments(self, parser):
        parser.add_argument(
            "--direction", choices=("push", "pull"), default="push", help="push=本地→对象存储；pull=对象存储→本地"
        )
        parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
        parser.add_argument("--verify", action="store_true", help="只校验目标完整性（存在性 + 大小）")
        parser.add_argument("--md5", action="store_true", help="校验时额外比对 md5（慢，需下载目标对象）")
        parser.add_argument(
            "--overwrite", action="store_true", help="目标存在但大小不一致时覆盖（默认记 conflict 不覆盖）"
        )
        parser.add_argument("--limit", type=int, default=None, help="最多处理文件数（缺省不限）")
        parser.add_argument("--batch", type=int, default=500, help="数据库遍历批大小")

    def handle(self, *args, **options):
        direction = options["direction"]
        try:
            local = util.get_local_storage()
            remote = util.get_remote_storage()
        except Exception as e:  # noqa: BLE001 配置不可用时给可读提示并退出非零
            self.stderr.write(f"[storage_migrate] 不可执行：{e}")
            raise SystemExit(1) from e

        source, target = (local, remote) if direction == "push" else (remote, local)
        stats = util.migrate_uploads(
            source,
            target,
            dry_run=options["dry_run"],
            limit=options["limit"],
            batch_size=options["batch"],
            overwrite=options["overwrite"],
            verify=options["verify"],
            check_md5=options["md5"],
        )
        self.stdout.write(util.summary_line(stats, direction, options["dry_run"], options["verify"]))
        for detail in stats["details"][:50]:
            suffix = f" {detail.get('error', '')}".rstrip()
            self.stdout.write(f"  - {detail['name']}: {detail['result']}{suffix}")
        if len(stats["details"]) > 50:
            self.stdout.write(f"  ... 其余 {len(stats['details']) - 50} 条明细已省略")

        problems = stats["failed"] + stats["verify_failed"] + stats["conflict"] + stats["missing_source"]
        if problems:
            raise SystemExit(1)
