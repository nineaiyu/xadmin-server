#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""定型 pgvector 列并建 HNSW 索引（构建任务完成后也会自动尝试）。

用法::

    python manage.py build_ai_vector_index          # 维度稳定时定型列 + 建 HNSW（幂等）
    python manage.py build_ai_vector_index --status # 只查看当前索引形态，不做变更

细节见 ``ai/utils/ai_vector_ddl.py``（维度混存自动反向定型、小语料 no-op、咨询锁串行）。
"""

from typing import Any

from django.core.management.base import BaseCommand

from ai.utils.ai_vector_ddl import ensure_vector_index, vector_index_state


class Command(BaseCommand):
    help = "Type the pgvector column and build the HNSW index when vector dims are stable "

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--status", action="store_true", help="只查看当前索引形态，不做变更")

    def handle(self, *args: Any, **options: Any) -> None:
        if options["status"]:
            state = vector_index_state()
        else:
            state = ensure_vector_index()
        self.stdout.write(
            self.style.SUCCESS(
                f"column={state.get('column_type') or 'missing'} hnsw={state.get('hnsw')} "
                f"dims={state.get('dims')} action={state.get('action')}"
                + (f" ({state['reason']})" if state.get("reason") else "")
            )
        )
