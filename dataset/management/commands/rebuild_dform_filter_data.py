#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按当前 schema 重建动态表单提交的物化筛选列（``filter_data``）。

用途：

- **存量兼容**：物化列引入前落库的提交没有 ``filter_data``，表单勾选「可筛选」后
  按该字段筛选不命中旧行——重建一次即可命中；
- 字段「可筛选」标记调整（新增/取消勾选）后批量回填。

口径：按当前 schema 重算（`build_filter_data` 与提交写入路径同一实现），仅更新有
差异的行（幂等，可重复执行）；``--dry-run`` 只统计不落库；提交数据本身（``data``）
不参与写回，重建失败不影响业务列。
"""

from typing import Any

from django.core.management.base import BaseCommand

from dataset.models.dform import DynamicFormSubmission
from dataset.utils.dform_filter import build_filter_data


class Command(BaseCommand):
    help = "按当前 schema 重建动态表单提交的物化筛选列（filter_data）"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--form", default="", help="只重建指定表单（pk）；缺省全部表单")
        parser.add_argument("--batch", type=int, default=500, help="批量写回大小（缺省 500）")
        parser.add_argument("--dry-run", action="store_true", help="只统计差异行，不落库")

    def handle(self, *args: Any, **options: Any) -> None:
        queryset = DynamicFormSubmission.objects.select_related("form").order_by("pk")
        form_pk = str(options["form"] or "").strip()
        if form_pk:
            queryset = queryset.filter(form_id=form_pk)
        batch_size = max(1, int(options["batch"]))
        dry_run = bool(options["dry_run"])

        scanned = updated = 0
        batch = []
        for submission in queryset.iterator(chunk_size=batch_size):
            scanned += 1
            materialized = build_filter_data(submission.form.schema, submission.data)
            if materialized == (submission.filter_data or {}):
                continue
            updated += 1
            if dry_run:
                continue
            submission.filter_data = materialized
            batch.append(submission)
            if len(batch) >= batch_size:
                DynamicFormSubmission.objects.bulk_update(batch, ["filter_data"])
                batch = []
        if batch:
            DynamicFormSubmission.objects.bulk_update(batch, ["filter_data"])

        action = "将重建" if dry_run else "已重建"
        self.stdout.write(
            self.style.SUCCESS(
                f"扫描 {scanned} 条提交，{action} {updated} 条物化筛选列" + ("（dry-run，未落库）" if dry_run else "")
            )
        )
