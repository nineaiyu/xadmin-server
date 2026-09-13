#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""同步 AI 助手知识库（ADR-023）：docs/ 分块入库，按 hash 幂等。"""

from django.core.management.base import BaseCommand

from system.utils.ai import sync_knowledge


class Command(BaseCommand):
    help = "Sync AI assistant knowledge base from repository docs"

    def handle(self, *args, **options):
        summary = sync_knowledge()
        self.stdout.write(self.style.SUCCESS(f"AI knowledge synced: {summary}"))
