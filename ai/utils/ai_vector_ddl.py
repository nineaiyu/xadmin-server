#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""pgvector HNSW 索引定型助手（ADR-074 目标形态的索引半边）。

维度策略：``embedding_vector`` 列保持**无维度** ``vector``（embedding 模型可换档，
迁移期/换档窗口内存量向量维度混存，带维度的列类型会让写入/回填直接失败）；本模块
在「全库向量维度稳定」时把列定型为 ``vector(N)`` 并建 HNSW 索引
（``vector_cosine_ops``，m=16 / ef_construction=64，ADR-074 口径）——

- 语料低于 ``INDEX_MIN_ROWS`` 时 no-op（精确扫描足够，避免无谓 DDL）；
- 维度混存（换档窗口）时**反向定型**：撤索引、列退回无维度（写入恢复任意维度）；
- 全程 ``pg_advisory_lock`` 串行化（构建任务完成回调与手工命令可能并发）；
- 所有 DDL 幂等（IF NOT EXISTS / IF EXISTS），失败由调用方告警不阻断。

调用面：``build_embeddings`` 成功后自动尝试 + ``build_ai_vector_index`` 命令手工执行。
"""

from common.utils import get_logger

logger = get_logger(__name__)

COLUMN = "embedding_vector"
INDEX_NAME = "aichunk_embedding_vector_hnsw"


def _chunk_table() -> str:
    """知识块表名取自模型 Meta（TG-3/ADR-080 表归域后随 ORM 单源，不再硬编码）。"""
    from ai.models import AiKnowledgeChunk

    return AiKnowledgeChunk._meta.db_table


#: HNSW 参数（ADR-074：m=16, ef_construction=64）
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 64
#: 语料规模门槛：低于该值精确扫描足够（≤2 万块上限内 P95 远低于 50ms 预算）
INDEX_MIN_ROWS = 1000

#: 维度定型状态（state/命令输出用）
ACTION_NOOP = "noop"
ACTION_TYPED = "typed"  # 列定型 vector(N)（未建索引）
ACTION_INDEXED = "indexed"  # 列定型 + HNSW 索引就绪
ACTION_UNTYPED = "untyped"  # 撤索引 + 列退回无维度（维度混存窗口）
ACTION_SKIPPED = "skipped"  # 不可判定/不可用（无向量、非 PG、扩展缺失）


def _cursor():
    from django.db import connection

    return connection.cursor()


def vector_index_state() -> dict:
    """当前索引形态（命令/状态页展示）：列类型、是否有 HNSW、向量维度分布。"""
    table = _chunk_table()
    with _cursor() as cursor:
        cursor.execute(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = %s::regclass AND attname = %s AND NOT attisdropped",
            [table, COLUMN],
        )
        row = cursor.fetchone()
        column_type = row[0] if row else ""
        # 索引按 search_path 解析（表/索引同库同 schema）
        cursor.execute("SELECT to_regclass(%s)", [INDEX_NAME])
        index_row = cursor.fetchone()
        cursor.execute(f"SELECT DISTINCT vector_dims({COLUMN}) FROM {table} WHERE {COLUMN} IS NOT NULL ORDER BY 1")
        dims = [int(db_row[0]) for db_row in cursor.fetchall()]
    return {
        "column_type": column_type,
        "hnsw": bool(index_row and index_row[0]),
        "dims": dims,
    }


def ensure_vector_index() -> dict:
    """按维度稳定性定型列 / 建（或撤）HNSW 索引，返回动作与形态（可安全反复执行）。"""
    from django.db import connection

    if connection.vendor != "postgresql":
        return {"action": ACTION_SKIPPED, "reason": "vendor"}
    try:
        state = vector_index_state()
    except Exception as exc:  # noqa: BLE001 扩展缺失（列类型不存在）时静默跳过
        return {"action": ACTION_SKIPPED, "reason": str(exc)}

    dims = state["dims"]
    if not dims:
        return {**state, "action": ACTION_SKIPPED, "reason": "no vectors"}
    if len(dims) > 1:
        # 换档窗口：维度混存必须撤索引退回无维度，否则新维度向量写不进去
        _drop_index_and_untype()
        logger.warning("vector dims mixed %s; hnsw index removed and column reverted to untyped vector", dims)
        return {**vector_index_state(), "action": ACTION_UNTYPED, "dims": dims}

    dim = dims[0]
    if state["hnsw"] and state["column_type"] == f"vector({dim})":
        return {**state, "action": ACTION_NOOP, "reason": "already typed and indexed", "dim": dim}
    with _cursor() as cursor:
        cursor.execute(f"SELECT count(*) FROM {_chunk_table()} WHERE {COLUMN} IS NOT NULL")
        total = int(cursor.fetchone()[0])
    if total < INDEX_MIN_ROWS:
        return {**state, "action": ACTION_NOOP, "reason": f"rows {total} < {INDEX_MIN_ROWS}", "dim": dim}

    _advisory_locked(_apply_typed_index, dim)
    return {**vector_index_state(), "action": ACTION_INDEXED, "dim": dim}


def ensure_column_accepts_dim(dim: int) -> dict:
    """写入前护栏：列已定型为其他维度时反向定型（撤索引 + 退回无维度）。

    定型后的列写入新维度向量会直接 DataError（模型换档场景），因此构建路径在
    确定本批维度后、写库前调用本函数；无维度列 / 维度匹配 / 非 PG 均为 no-op。
    """
    from django.db import connection

    if connection.vendor != "postgresql":
        return {"action": ACTION_SKIPPED, "reason": "vendor"}
    with _cursor() as cursor:
        column_type = _column_type(cursor)
    if column_type in ("", "vector"):
        return {"action": ACTION_NOOP, "reason": "column untyped"}
    if column_type == f"vector({dim})":
        return {"action": ACTION_NOOP, "reason": "dim matches"}
    _advisory_locked(_drop_index_and_untype)
    logger.warning("vector column typed %s conflicts with incoming dim %s; reverted to untyped", column_type, dim)
    return {**vector_index_state(), "action": ACTION_UNTYPED}


def _apply_typed_index(dim: int) -> None:
    """定型列 + 建索引（持锁调用；两步都幂等）。"""
    table = _chunk_table()
    with _cursor() as cursor:
        if _column_type(cursor) == "vector":  # 无维度形态才需要 ALTER（带维度则幂等跳过）
            cursor.execute(f"ALTER TABLE {table} ALTER COLUMN {COLUMN} TYPE vector({dim})")
        cursor.execute(
            f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} ON {table} "
            f"USING hnsw ({COLUMN} vector_cosine_ops) WITH (m = {HNSW_M}, ef_construction = {HNSW_EF_CONSTRUCTION})"
        )


def _drop_index_and_untype() -> None:
    """换档窗口的反向定型（持锁调用；两步都幂等）。"""
    with _cursor() as cursor:
        cursor.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
        if _column_type(cursor) != "vector":
            cursor.execute(f"ALTER TABLE {_chunk_table()} ALTER COLUMN {COLUMN} TYPE vector")


def _column_type(cursor) -> str:
    cursor.execute(
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
        "WHERE attrelid = %s::regclass AND attname = %s AND NOT attisdropped",
        [_chunk_table(), COLUMN],
    )
    row = cursor.fetchone()
    return row[0] if row else ""


def _advisory_locked(fn, *args):
    """会话级咨询锁串行化 DDL（锁键 = 固定命名空间，同连接加锁/解锁）。"""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(hashtext('xadmin_ai_vector_ddl'))")
        try:
            fn(*args)
        finally:
            cursor.execute("SELECT pg_advisory_unlock(hashtext('xadmin_ai_vector_ddl'))")
