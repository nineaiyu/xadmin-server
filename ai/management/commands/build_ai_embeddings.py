#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""构建知识库向量（显式触发；未配置 embedding 档案时给出引导并退出）。

用法::

    python manage.py build_ai_embeddings              # 只补缺失/陈旧块（幂等）
    python manage.py build_ai_embeddings --force      # 全量重算
    python manage.py build_ai_embeddings --dry-run    # 只统计待构建条数
    python manage.py build_ai_embeddings --document upload/xxx.md --batch-size 16

与知识库页「构建向量」按钮同源（``ai.utils.ai_embeddings.build_embeddings``）；
向量配置见 AI 配置页（用途 = 文本向量化）的激活档案。
"""

from typing import Any

from django.core.management.base import BaseCommand

from ai.utils.ai_embeddings import build_embeddings, vector_stats


class Command(BaseCommand):
    help = "Build knowledge base embeddings (requires an active embedding AI profile)"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--force", action="store_true", help="全量重算（忽略既有向量）")
        parser.add_argument("--dry-run", action="store_true", help="只统计待构建条数，不调用供应商")
        parser.add_argument("--document", default="", help="限定单个文档（文档存储路径，如 docs/README.md）")
        parser.add_argument("--batch-size", type=int, default=32, help="单批文本条数（1-256，默认 32）")

    def handle(self, *args: Any, **options: Any) -> None:
        document = None
        path = (options["document"] or "").strip()
        if path:
            from ai.models.ai import AiKnowledgeDocument

            document = AiKnowledgeDocument.objects.filter(path=path).first()
            if document is None:
                self.stderr.write(self.style.ERROR(f"document not found: {path}"))
                return
        summary = build_embeddings(
            document=document,
            force=bool(options["force"]),
            batch_size=int(options["batch_size"] or 32),
            dry_run=bool(options["dry_run"]),
        )
        if not summary["enabled"]:
            self.stderr.write(
                self.style.WARNING(
                    "embedding is not enabled: configure an active AI profile with purpose=embedding first"
                )
            )
            return
        self.stdout.write(f"embedding build summary: {summary}")
        self.stdout.write(f"vector stats: {vector_stats()}")
        if not summary["ok"]:
            self.stderr.write(self.style.ERROR(f"build stopped: {summary['detail']}"))
