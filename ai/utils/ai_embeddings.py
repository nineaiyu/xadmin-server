#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""知识库向量检索：embedding 构建、pgvector 检索与 RRF 混合融合。

口径（ADR-065 建立混合检索，ADR-074 落地 pgvector，2026-10-02 交付）：
- **启用条件**：存在 ``purpose=embedding`` 的激活档案；无档案 = 词频检索零变化；
- **构建**：管理端显式触发（知识库页按钮 / ``build_ai_embeddings`` 命令，支持全量
  force / dry_run）**+ 正文变更后自动增量补齐**（``schedule_auto_rebuild``，ADR-082：
  上传/覆盖、停用再启用、仓库同步三个挂点，只补缺失/陈旧块，无档案/无待建/构建中
  静默跳过），批量调用后按块落库——``embedding``（float32 二进制，迁移窗口保留，
  回滚=代码回退）与 ``embedding_vector``（pgvector 列）**双写**；正文变更后的旧向量
  按 ``embedding_hash`` 判定陈旧，补齐完成前向量通道跳过该块（词频通道兜底）；
- **检索**：SQL 余弦（``embedding <=> query``，``CosineDistance`` 升序）替代进程内
  索引——新鲜度（``embedding_hash == content_hash``）、模型一致性、维度一致性都在
  SQL WHERE 内收敛（维度不一致的行直接排除，复刻旧内存索引的逐块 skip 语义）；
  返回结构保持 ``[(score, pk)]``（消费方契约不变）；向量链路任一异常（未配置 /
  扩展缺失 / 网络失败 / 超容量）都回退词频结果——问答可用性不被向量可用性绑定；
- **索引**：列保持无维度，语料规模小的时候精确扫描（≤2 万块，P95 ≪ 50ms）；
  维度稳定后由 ``ai_vector_ddl.ensure_vector_index`` 定型列并建 HNSW
  （m=16, ef_construction=64，构建任务完成后自动尝试，也可用
  ``build_ai_vector_index`` 命令手工执行）；
- **缓存**：仅保留查询向量短缓存（评测集连跑 / 重复提问省一次远端调用）与
  ``index_meta`` 元数据行 TTL 缓存（可用性判定不再每次全表拉元数据）。
"""

import threading
import time
from typing import Any

from django.db import transaction
from django.db.models import F

from ai.utils.ai_embedding_math import (  # noqa: F401  (纯函数拆出，再导出保持调用面)
    RRF_K,
    TOKEN_WEIGHT,
    VECTOR_WEIGHT,
    cosine_similarity,
    decode_vector,
    encode_vector,
    rrf_fuse,
)
from common.utils import get_logger

logger = get_logger(__name__)

#: 单次构建的批大小（按供应商 /embeddings 的常见批量上限取保守值）
EMBED_BATCH_SIZE = 32
#: 向量通道容量上限：超过即停用向量通道（与分词索引同口径，避免异常规模拖垮查询）
MAX_INDEXED_VECTORS = 20000
#: 参与融合的向量候选数（取相似度前 N 名；词频通道候选为全部达标块）
VECTOR_CANDIDATES = 50
#: 查询向量短缓存条数（评测集连跑 / 重复提问省一次远端调用）
QUERY_CACHE_SIZE = 128
#: 向量通道的最小可用条数（低于该值向量排名噪声大，直接走词频）
MIN_INDEXED_VECTORS = 5

_LOCK = threading.Lock()
_QUERY_CACHE: dict = {}
_OVERFLOW_WARNED = False


# ------------------------------------------------------------------ 可用性判定


def _load_meta_rows():
    from ai.models.ai import AiKnowledgeChunk

    return list(
        AiKnowledgeChunk.objects.exclude(embedding__isnull=True)
        .order_by("source_path", "chunk_index")
        .values_list("pk", "content_hash", "embedding_hash", "embedding_model", "embedding_dim")
    )


def vector_index():
    """新鲜向量可用性探针：``{pk: dim}``（键序稳定）；不可用时返回 None。

    可用性判据与内存索引时代一致（ADR-074 迁移前的口径原样保留）：
    存在 embedding 档案 + 已向量化总量未超容量 + 新鲜条数达到最小可用条数。
    新鲜 = ``embedding_hash == content_hash`` 且模型与当前档案一致（陈旧向量
    不参与向量通道，避免用旧正文的语义召回当前问题）。

    返回值只作为「向量通道是否可用 + 候选集合规模」的判定面（检索本体走 SQL，
    不再经该 dict 逐块算余弦）；元数据行经 ``index_meta`` 短 TTL 缓存。
    """
    from ai.utils.ai_config import embedding_credentials
    from ai.utils.index_meta import SCOPE_VECTOR_META, cached_meta_rows

    global _OVERFLOW_WARNED

    credentials = embedding_credentials()
    if credentials is None:
        return None
    model = str(credentials.get("model") or "")
    rows = cached_meta_rows(SCOPE_VECTOR_META, _load_meta_rows)
    if len(rows) > MAX_INDEXED_VECTORS:
        if not _OVERFLOW_WARNED:
            logger.warning(
                "knowledge vector channel disabled: %s vectors exceed capacity %s", len(rows), MAX_INDEXED_VECTORS
            )
            _OVERFLOW_WARNED = True
        return None

    usable = {
        pk: int(dim or 0)
        for pk, content_hash, embedding_hash, embedding_model, dim in rows
        if embedding_hash == content_hash and embedding_model == model
    }
    if len(usable) < MIN_INDEXED_VECTORS:
        return None
    return usable


def invalidate_vector_index() -> None:
    """清查询向量缓存与元数据签名缓存（构建完成后调用，本进程立即生效）。

    进程内向量索引已随 pgvector 落地退役（SQL 直查永远读到最新已提交数据，
    无需索引失效）；本函数保留原调用面，只清仍存在的两层短缓存。
    """
    from ai.utils.index_meta import SCOPE_VECTOR_META, invalidate_index_meta

    with _LOCK:
        _QUERY_CACHE.clear()
    invalidate_index_meta(SCOPE_VECTOR_META)


# ------------------------------------------------------------------ 检索（向量通道 / 混合）


def _embed_query(client, question: str):
    """查询向量（带条数上限的短缓存；失败返回 None，由调用方回退词频）。"""
    key = (client.model, question)
    with _LOCK:
        cached = _QUERY_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        vectors = client.embed([question])
    except Exception as exc:  # noqa: BLE001 向量链路任一异常都回退词频（可用性优先）
        logger.warning("ai embedding query failed: %s", exc)
        return None
    if not vectors or not vectors[0]:
        return None
    vector = vectors[0]
    with _LOCK:
        if len(_QUERY_CACHE) >= QUERY_CACHE_SIZE:
            _QUERY_CACHE.clear()
        _QUERY_CACHE[key] = vector
    return vector


def search_vectors(question: str, top_k: int = VECTOR_CANDIDATES) -> list:
    """向量通道排名（pk 列表，余弦降序）；任何不可用情形返回空列表。

    SQL 口径（ADR-074）：候选 = 新鲜（embedding_hash == content_hash）且模型与
    当前档案一致、维度与查询向量一致、pgvector 列非空的块；排序 = ``embedding
    <=> query`` 升序（余弦距离 = 1 - 余弦相似度，序与旧实现逐块余弦降序一致）。
    扩展缺失 / 列缺失等数据库层异常在此吞掉（返回 []），由调用方回退词频。
    """
    from ai.utils.ai_config import embedding_credentials

    credentials = embedding_credentials()
    if credentials is None:
        return []
    if not vector_index():
        return []
    from common.sdk.ai.embeddings import EmbeddingClient

    client = EmbeddingClient(credentials)
    query_vector = _embed_query(client, question)
    if query_vector is None:
        return []
    try:
        return _rank_vectors(model=str(client.model), query_vector=query_vector, top_k=top_k)
    except Exception as exc:  # noqa: BLE001 数据库层异常（扩展缺失/列缺失）回退词频
        logger.warning("ai vector search failed, fallback to token channel: %s", exc)
        return []


def _rank_vectors(*, model: str, query_vector: list, top_k: int) -> list:
    """SQL 余弦排名本体（独立成函数便于测试注入）。"""
    from pgvector.django import CosineDistance

    from ai.models.ai import AiKnowledgeChunk

    qs = (
        AiKnowledgeChunk.objects.exclude(embedding_vector__isnull=True)
        .filter(embedding_hash=F("content_hash"), embedding_model=model, embedding_dim=len(query_vector))
        .annotate(distance=CosineDistance("embedding_vector", query_vector))
        .order_by("distance", "pk")
    )
    return [pk for pk in qs.values_list("pk", flat=True)[: max(1, top_k)]]


def hybrid_rank(question: str, token_ranked: list, top_k: int):
    """混合融合入口：返回 ``[(score, pk)]``；向量通道不可用时返回 None（走词频原路径）。"""
    vector_ranked = search_vectors(question)
    if not vector_ranked:
        return None
    return rrf_fuse(token_ranked, vector_ranked, top_k)


# ------------------------------------------------------------------ 构建（显式触发）


def _pending_rows(qs, model: str, force: bool):
    """待构建块：force 全量；否则仅「未向量化 / 模型变更 / 正文 hash 变更」的块。"""
    pending = []
    skipped = 0
    for pk, content_hash, embedding_hash, embedding_model, content in qs.values_list(
        "pk", "content_hash", "embedding_hash", "embedding_model", "content"
    ):
        if force or embedding_hash != content_hash or embedding_model != model:
            pending.append((pk, content_hash, content or ""))
        else:
            skipped += 1
    return pending, skipped


def build_embeddings(
    document=None, force: bool = False, batch_size: int = EMBED_BATCH_SIZE, dry_run: bool = False, progress_cb=None
) -> dict:
    """批量构建/刷新知识块向量，返回摘要（``enabled/ok/model/dim/total/embedded/skipped/failed``）。

    - ``document``：限定单个文档（``source_path`` 匹配）；缺省全库；
    - ``force``：忽略既有向量全量重算；缺省只补缺失/陈旧块（幂等、可反复执行）；
    - ``dry_run``：只统计待构建条数，不调用供应商、不写库；
    - ``progress_cb``：批次级进度回调 ``cb(percent, stage, embedded)``（异步任务
      经 embedding_progress 通道上报；同步调用/命令不传 = 零开销）；
    - 失败语义：单批失败即停止（供应商多半整体不可用），已成功的批次保留，
      ``ok=False`` + ``failed`` 计数返回，调用方据此给出可读提示；
    - 写入为 ``embedding``（二进制）+ ``embedding_vector``（pgvector）**双写**：
      二进制列在迁移窗口保留（ADR-074 步骤④后半段「删二进制列与内存索引代码」
      登记为稳定一个版本后的独立清理项），回滚 = 代码回退，数据无需重建。
    """
    from ai.models.ai import AiKnowledgeChunk
    from ai.utils.ai_config import embedding_credentials

    summary: dict[str, Any] = {
        "enabled": False,
        "ok": True,
        "model": "",
        "dim": 0,
        "total": 0,
        "embedded": 0,
        "skipped": 0,
        "failed": 0,
        "detail": "",
    }
    credentials = embedding_credentials()
    if credentials is None:
        summary["detail"] = "no active embedding profile"
        return summary

    from common.sdk.ai.embeddings import AiSdkError, EmbeddingClient

    client = EmbeddingClient(credentials)
    model = client.model
    qs = AiKnowledgeChunk.objects.all()
    if document is not None:
        qs = qs.filter(source_path=document.path)
    summary["enabled"] = True
    summary["model"] = model
    summary["total"] = qs.count()

    pending, skipped = _pending_rows(qs.order_by("source_path", "chunk_index"), model, force)
    summary["skipped"] = skipped
    if dry_run or not pending:
        return summary

    def _report(done: int, stage: str) -> None:
        if progress_cb is not None:
            progress_cb(int(done * 100 / max(1, len(pending))), stage=stage, embedded=done)

    _report(0, stage="embed")
    batch_size = max(1, min(int(batch_size or EMBED_BATCH_SIZE), 256))
    started = time.monotonic()
    usage_total: dict = {}
    dim = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        try:
            vectors = client.embed([content for _pk, _hash, content in batch])
        except AiSdkError as exc:
            summary["ok"] = False
            summary["failed"] = len(pending) - summary["embedded"]
            summary["detail"] = str(exc)
            logger.warning("build knowledge embeddings stopped: %s", exc)
            break
        batch_dim = len(vectors[0])
        if any(len(vector) != batch_dim for vector in vectors) or (dim and batch_dim != dim):
            summary["ok"] = False
            summary["failed"] = len(pending) - summary["embedded"]
            summary["detail"] = "inconsistent embedding dimension"
            logger.warning("build knowledge embeddings stopped: dimension mismatch")
            break
        dim = batch_dim
        # 写入前护栏：列已定型为其他维度（模型换档）时先反向定型，否则本批写库必失败
        try:
            from ai.utils.ai_vector_ddl import ensure_column_accepts_dim

            ensure_column_accepts_dim(dim)
        except Exception:  # noqa: BLE001 反向定型失败维持原行为（写库报错计入 failed）
            logger.warning("revert typed vector column failed", exc_info=True)
        rows = []
        for (pk, content_hash, _content), vector in zip(batch, vectors, strict=False):
            rows.append(
                AiKnowledgeChunk(
                    pk=pk,
                    content_hash=content_hash,
                    embedding_hash=content_hash,
                    embedding_model=model,
                    embedding_dim=dim,
                    embedding=encode_vector(vector),
                    embedding_vector=vector,
                )
            )
        AiKnowledgeChunk.objects.bulk_update(
            rows,
            ["embedding", "embedding_vector", "embedding_model", "embedding_hash", "embedding_dim"],
            batch_size=batch_size,
        )
        summary["embedded"] += len(rows)
        _report(summary["embedded"], stage="embed")
        usage_total = _accumulate_usage(usage_total, client.last_usage)
    summary["dim"] = dim
    invalidate_vector_index()
    if summary["embedded"]:
        # 维度稳定时尝试定型列并建 HNSW（小语料 no-op；失败只告警，检索走精确扫描）
        try:
            from ai.utils.ai_vector_ddl import ensure_vector_index

            ensure_vector_index()
        except Exception:  # noqa: BLE001 索引定型失败不影响构建结果与检索可用性
            logger.warning("ensure pgvector hnsw index failed; retrieval uses exact scan", exc_info=True)
        _record_build_usage(usage_total, model, started, summary)
    return summary


# ------------------------------------------------------------------ 自动重算（正文变更触发）


def _has_pending_chunks(source_path: str | None, model: str) -> bool:
    """是否存在待建块（缺向量 / 正文变更 / 模型不符），判定与 ``_pending_rows`` 同口径。

    ``embedding_hash`` 为非空默认 ""（未构建块），与 64 位十六进制 ``content_hash``
    恒不相等，SQL 比较无 NULL 三值逻辑坑。
    """
    from ai.models.ai import AiKnowledgeChunk

    qs = AiKnowledgeChunk.objects.all()
    if source_path:
        qs = qs.filter(source_path=source_path)
    return qs.exclude(embedding_hash=F("content_hash"), embedding_model=model).exists()


def schedule_auto_rebuild(document=None) -> bool:
    """正文变更后自动补齐向量（ADR-082 / F7-6），返回是否实际调度。

    与手工构建共用同一条状态机（单飞锁 → ``build_embeddings_task`` → 进度/终态/释放），
    增量口径（force=False）只补缺失/陈旧块。三重前置，任一不满足即静默跳过：

    - embedding 档案未配置：向量链路未启用，部署行为零变化；
    - 无待建块：未变内容重传等场景不空转占锁（避免挤掉手工构建入口）；
    - 构建单飞锁被占：不排队，防重复消耗供应商预算（进行中的全量构建会顺带覆盖）。

    模型换档不做自动全量重嵌（成本不可控，走构建按钮 force）；旧模型块由
    ``vector_index`` 的模型一致性判据跳过（词频通道兜底）——自动调度只保证
    「正文新鲜度」，不保证「模型一致性」。
    """
    from django.conf import settings

    from ai.utils.ai_config import embedding_credentials
    from ai.utils.embedding_progress import try_acquire_lock

    credentials = embedding_credentials()
    if credentials is None:
        return False
    model = str(credentials.get("model") or "")
    source_path = getattr(document, "path", None) if document is not None else None
    if not _has_pending_chunks(source_path, model):
        return False
    if not try_acquire_lock():
        logger.info("embedding auto rebuild skipped: a build is already running")
        return False

    from ai.tasks import build_embeddings_task

    task_args = [str(document.pk) if document is not None else "", False]
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        # 测试/E2E：eager 下 apply_async 不执行，改 apply 同步跑完（与手工构建入口同口径）
        build_embeddings_task.apply(args=task_args)
    else:
        transaction.on_commit(lambda: build_embeddings_task.apply_async(args=task_args))
    return True


def _accumulate_usage(previous: dict, usage) -> dict:
    """累计多批调用的 token 用量（供应商 usage 字段缺失按 0）。"""
    data = previous or {}
    current = usage if isinstance(usage, dict) else {}
    for key in ("prompt_tokens", "total_tokens"):
        data[key] = int(data.get(key) or 0) + int(current.get(key) or 0)
    return data


def _record_build_usage(usage: dict, model: str, started: float, summary: dict) -> None:
    """构建用量入账本（不归因个人：构建是管理端批处理，不占用个人配额）。"""
    from ai.utils.ai_usage import record_usage

    record_usage(
        None,
        "embedding",
        usage=usage,
        duration_ms=int((time.monotonic() - started) * 1000),
        ok=bool(summary.get("ok")),
        detail=f"embedded={summary.get('embedded', 0)} failed={summary.get('failed', 0)}",
        model=model,
    )


def vector_stats() -> dict:
    """向量通道状态（知识库页提示 / 命令输出）：总量、已向量化、可用、陈旧。"""
    from ai.models.ai import AiKnowledgeChunk
    from ai.utils.ai_config import embedding_credentials

    credentials = embedding_credentials()
    total = AiKnowledgeChunk.objects.count()
    data = {
        "enabled": credentials is not None,
        "model": "",
        "dim": 0,
        "total": total,
        "embedded": 0,
        "fresh": 0,
        "stale": 0,
    }
    if credentials is None:
        return data
    model = str(credentials.get("model") or "")
    data["model"] = model
    embedded = AiKnowledgeChunk.objects.exclude(embedding__isnull=True)
    data["embedded"] = embedded.count()
    fresh = embedded.filter(embedding_model=model).filter(embedding_hash=F("content_hash"))
    data["fresh"] = fresh.count()
    data["stale"] = max(data["embedded"] - data["fresh"], 0)
    data["dim"] = embedded.order_by("-synced_at").values_list("embedding_dim", flat=True).first() or 0
    return data
