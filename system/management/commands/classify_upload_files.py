#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""存量上传文件分类回填。

上传自动分类（`system/utils/upload_category.py`）只对新上传生效——历史上传
记录的 category 仍为空，因此「存储面板分类分布」对存量数据不准确。本命令按
同一套推断规则批量整理（含回收站记录，恢复后分类仍在）。

推断只写字典中存在的分类（字典是分类唯一事实来源）：无法归类且字典未配置
「其他」时保持原值，绝不写字典外取值。

用法：
    python manage.py classify_upload_files --dry-run   # 只统计，不改库
    python manage.py classify_upload_files             # 回填未分类（含空串）记录
    python manage.py classify_upload_files --all       # 全部重新推断（覆盖人工分类，谨慎）
"""

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from common.utils import get_logger
from system.models import UploadFile
from system.utils.upload_category import resolve_upload_category

logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Backfill upload file category by MIME/extension (dict-bounded values only)"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Only report, do not write")
        parser.add_argument("--all", action="store_true", help="Re-classify records that already have a category")
        parser.add_argument("--batch-size", type=int, default=1000, help="Bulk update batch size")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]
        # 含回收站：软删除记录恢复后分类仍然有效；默认只动未分类（含历史空串）
        queryset = UploadFile.all_objects.all()
        if not options["all"]:
            queryset = queryset.filter(Q(category__isnull=True) | Q(category=""))

        changed = []
        skipped = 0
        for upload in queryset.iterator(chunk_size=batch_size):
            category = resolve_upload_category(upload.filename, upload.mime_type)
            if category and category != upload.category:
                upload.category = category
                # bulk_update 不触发 auto_now，手动补 updated_time（列表排序/审计口径一致）
                upload.updated_time = timezone.now()
                changed.append(upload)
            else:
                skipped += 1

        if changed and not dry_run:
            UploadFile.all_objects.bulk_update(changed, ["category", "updated_time"], batch_size=batch_size)
        verb = "would classify" if dry_run else "classified"
        summary = f"{verb}: {len(changed)}, unchanged: {skipped}"
        self.stdout.write(self.style.SUCCESS(summary))
        logger.info("classify upload files. %s", summary)
