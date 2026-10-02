#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""知识库向量列迁 pgvector（ADR-074 落地步骤①②）。

- 步骤①：``CREATE EXTENSION IF NOT EXISTS vector``——vendor 守护 + 失败仅告警
  （与 system/migrations/0004 的 pg_trgm 同口径；扩展不可用时列建不出、检索/构建
  运行期报错并回退词频通道，见 ai/utils/ai_embeddings.py 的 fail-open 设计）；
- 步骤②：``embedding_vector`` 向量列（无维度 ``vector``：embedding 模型可换档，
  维度稳定后由 ai/utils/ai_vector_ddl.py 的 ensure_vector_index 定型并建 HNSW）。
  DDL 手写 SQL（列存在性幂等）+ SeparateDatabaseAndState：扩展缺失的库只跳过 DDL，
  不阻断 migrate（状态仍登记该字段，ORM 触达时报错由检索层兜底）；
- 步骤③：存量 float32 二进制 → 向量列分批回填（batch 500，ADR-074 口径；
  1200 块量级秒级完成）。反向迁移保留向量列数据（二进制列仍在，双写窗口未关闭）。
"""

import logging

import pgvector.django.vector
from django.db import migrations

from ai.utils.ai_embedding_math import decode_vector

logger = logging.getLogger(__name__)

#: 存量回填分批大小（ADR-074：batch_size=500）
BACKFILL_BATCH_SIZE = 500


def _is_postgresql(schema_editor) -> bool:
    return schema_editor.connection.vendor == "postgresql"


def create_vector_extension(apps, schema_editor):
    if not _is_postgresql(schema_editor):
        return
    with schema_editor.connection.cursor() as cursor:
        try:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception:  # noqa: BLE001 扩展不可用仅告警，后续步骤逐级降级（见模块 docstring）
            logger.warning(
                "CREATE EXTENSION vector failed; pgvector retrieval will fall back to token channel", exc_info=True
            )


def add_vector_column(apps, schema_editor):
    if not _is_postgresql(schema_editor):
        return
    table = schema_editor.connection.ops.quote_name("system_aiknowledgechunk")
    column = schema_editor.connection.ops.quote_name("embedding_vector")
    with schema_editor.connection.cursor() as cursor:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} vector")
        except Exception:  # noqa: BLE001 扩展缺失等场景仅告警：状态已登记，运行期回退词频
            logger.warning("ADD COLUMN embedding_vector vector failed; vector channel disabled", exc_info=True)


def remove_vector_column(apps, schema_editor):
    if not _is_postgresql(schema_editor):
        return
    table = schema_editor.connection.ops.quote_name("system_aiknowledgechunk")
    column = schema_editor.connection.ops.quote_name("embedding_vector")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")


def backfill_vectors(apps, schema_editor):
    """存量 float32 二进制 → 向量列分批回填（解码失败/扩展缺失的批次告警跳过）。"""
    if not _is_postgresql(schema_editor):
        return
    chunk = apps.get_model("ai", "AiKnowledgeChunk")
    pks = list(chunk.objects.exclude(embedding__isnull=True).values_list("pk", flat=True))
    done = 0
    for start in range(0, len(pks), BACKFILL_BATCH_SIZE):
        batch = []
        for pk, blob in chunk.objects.filter(pk__in=pks[start : start + BACKFILL_BATCH_SIZE]).values_list(
            "pk", "embedding"
        ):
            vector = decode_vector(blob)
            if vector is None:
                continue
            batch.append(chunk(pk=pk, embedding_vector=vector))
        try:
            chunk.objects.bulk_update(batch, ["embedding_vector"], batch_size=BACKFILL_BATCH_SIZE)
        except Exception:  # noqa: BLE001 列缺失（扩展未装成）时整批跳过，不阻断升级
            logger.warning("vector backfill batch skipped (vector column unavailable?)", exc_info=True)
            return
        done += len(batch)
    logger.info("vector backfill completed: %s rows", done)


class Migration(migrations.Migration):
    dependencies = [
        ("ai", "0004_mcpserver_expose_to_ai"),
    ]

    operations = [
        migrations.RunPython(create_vector_extension, migrations.RunPython.noop),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="aiknowledgechunk",
                    name="embedding_vector",
                    field=pgvector.django.vector.VectorField(
                        blank=True, editable=False, null=True, verbose_name="Embedding vector"
                    ),
                ),
            ],
            database_operations=[
                migrations.RunPython(add_vector_column, remove_vector_column),
            ],
        ),
        migrations.RunPython(backfill_vectors, migrations.RunPython.noop),
    ]
