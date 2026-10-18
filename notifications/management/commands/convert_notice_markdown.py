#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""存量富文本公告转 Markdown（有损，默认只报告不写库）。

背景：公告新增了 ``message_format``，存量行默认 ``html``。历史上由 AI 发布的
公告正文已经是 Markdown，只是被当作 HTML 存储与展示（页面上直接出现 ``**``、
``#`` 与表格源码）。本命令用于按需修复这批存量数据：

- 默认 dry-run，仅输出待转换条数；
- ``--apply`` 才写库（``message`` 改为 html2text 结果，``message_format=markdown``）；
- 支持 ``--ids`` 精准处理、``--limit`` 分批执行。

**有损提示**：HTML→Markdown 会丢失样式、视频/附件等富文本结构，默认不批量执行；
命令只处理 ``message_format=html`` 且正文非空的行，可反复执行（转换后格式为
markdown，不会再次命中）。
"""

from typing import Any

from django.core.management.base import BaseCommand

from common.utils.common import convert_html_to_markdown
from notifications.models import MessageContent


class Command(BaseCommand):
    help = "Convert existing HTML notices to Markdown (lossy; dry-run by default)"

    def add_arguments(self, parser: Any) -> None:
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--apply", action="store_true", help="Write changes (default: report only)")
        mode.add_argument("--dry-run", action="store_true", help="Only report, do not write (default)")
        parser.add_argument("--ids", default="", help="Comma separated primary keys (default: all HTML notices)")
        parser.add_argument("--limit", type=int, default=0, help="Max rows to process (0 = no limit)")

    def handle(self, *args: Any, **options: Any) -> None:
        apply = options["apply"]
        raw_ids = str(options["ids"] or "").strip()
        limit = int(options["limit"] or 0)

        queryset = MessageContent.all_objects.filter(message_format=MessageContent.MessageFormat.HTML)
        queryset = queryset.exclude(message__isnull=True).exclude(message="")
        if raw_ids:
            pks = [item.strip() for item in raw_ids.split(",") if item.strip()]
            queryset = queryset.filter(pk__in=pks)
        queryset = queryset.order_by("pk")
        if limit > 0:
            queryset = queryset[:limit]

        converted = 0
        skipped = 0
        for notice in queryset.iterator(chunk_size=200):
            markdown = convert_html_to_markdown(notice.message or "").strip()
            if not markdown:
                skipped += 1
                continue
            if apply:
                notice.message = markdown
                notice.message_format = MessageContent.MessageFormat.MARKDOWN
                notice.save(update_fields=["message", "message_format", "updated_time"])
            converted += 1

        verb = "converted" if apply else "would convert"
        summary = f"{verb}: {converted}, skipped: {skipped}"
        self.stdout.write(self.style.SUCCESS(summary))
