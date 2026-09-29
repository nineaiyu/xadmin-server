#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库向量检索：embedding 构建、缓存索引与 RRF 混合融合。

口径（与 ADR-037「若将来升级」预研一致，落地见 ADR-065）：
- **启用条件**：存在 ``purpose=embedding`` 的激活档案；无档案 = 词频检索零变化，
  不新增部署依赖（OpenAI 兼容 ``/embeddings`` + Python 侧余弦，语料超 1 万块再评估
  pgvector，须另立 ADR）；
- **构建**：管理端显式触发（知识库页按钮 / ``build_ai_embeddings`` 命令），批量调用
  后按块落库（float32 小端二进制）；正文变更后旧向量按 ``embedding_hash`` 判定陈旧，
  向量通道跳过该块（词频通道兜底），不做隐式重算——构建耗时与 API 成本可见可控；
- **检索**：词频排名 + 向量排名 **RRF 融合**（k=60；词频权重 1.0 / 向量权重 0.5，
  见 ``TOKEN_WEIGHT`` 注释），返回结构保持 ``[(score, pk)]``（消费方契约不变）；
  向量链路任一异常（未配置 / 网络失败 / 维度不一致 / 超容量）都回退词频结果——
  问答可用性不被向量可用性绑定；
- **内存**：向量索引与分词索引同款增量缓存（签名 = 正文 hash + 向量 hash + 模型 +
  维度，按模型 ordering 对齐），超容量停用缓存（该次查询退回词频）。
"""

import array
import math
import threading
import time
from typing import Any, NamedTuple

from common.utils import get_logger

logger = get_logger(__name__)

#: 单次构建的批大小（按供应商 /embeddings 的常见批量上限取保守值）
EMBED_BATCH_SIZE = 32
#: 向量索引容量上限：超过即停用向量通道（与分词索引同口径，避免异常规模内存失控）
MAX_INDEXED_VECTORS = 20000
#: RRF 融合常数（Cormack 等 2009 原文取值；排名越靠前权重越高）
RRF_K = 60
#: 通道权重：词频是质量基线（ADR-037 实测 hit@5 97.2%），向量为语义补充——
#: 单通道向量命中排在词频命中之后（弱 embedding 模型不会稀释基线质量），
#: 词频无命中/命中稀少时（改写式提问、同义表述）由向量通道补齐 top-k
TOKEN_WEIGHT = 1.0
VECTOR_WEIGHT = 0.5
#: 参与融合的向量候选数（取相似度前 N 名；词频通道候选为全部达标块）
VECTOR_CANDIDATES = 50
#: 查询向量短缓存条数（评测集连跑 / 重复提问省一次远端调用）
QUERY_CACHE_SIZE = 128
#: 向量通道的最小可用条数（低于该值向量排名噪声大，直接走词频）
MIN_INDEXED_VECTORS = 5
#: 变更块批量回填分片大小（避免超长 IN 查询）
LOAD_BATCH_SIZE = 200

_LOCK = threading.Lock()
_INDEX: dict = {}
_QUERY_CACHE: dict = {}
_OVERFLOW_WARNED = False


class VectorEntry(NamedTuple):
    """单块向量（float32 数组，只读使用；可安全跨线程共享）。"""

    signature: str
    norm: float
    vector: array.array


# ------------------------------------------------------------------ 编解码 / 相似度


def encode_vector(values) -> bytes:
    """float 序列 → float32 小端二进制（落库形态）。"""
    return array.array("f", [float(value) for value in values]).tobytes()


def decode_vector(blob):
    """落库二进制 → float32 数组；长度非法（非 4 字节对齐）返回 None。"""
    if blob is None:
        return None
    raw = bytes(blob)
    if not raw or len(raw) % 4:
        return None
    values = array.array("f")
    values.frombytes(raw)
    return values


def cosine_similarity(left, right) -> float:
    """余弦相似度（两侧等长且非零；任一侧为空/长度不一致返回 0）。"""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right, strict=False):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


def rrf_fuse(token_ranked: list, vector_ranked: list, top_k: int) -> list:
    """RRF 融合两个排名列表，返回 ``[(score, pk)]``（score 降序）。

    ``token_ranked`` / ``vector_ranked`` 为 pk 列表（名次从 1 开始计）；同一块在两个
    通道都靠前时得分叠加。仅出现在向量通道的块同样进入结果（语义召回的价值位），
    但权重更低（``VECTOR_WEIGHT``）：词频命中始终排在单通道向量命中之前。

    同分按「最佳名次 → 向量名次 → pk」稳定排序：RRF 同分无法判优劣（两侧名次互换
    等价的常见情形），该口径让另一侧也更靠前的块胜出，同时保证结果可复现。
    """
    scores: dict = {}
    best_rank: dict = {}
    vector_rank: dict = {}
    for channel, ranked, weight in (("token", token_ranked, TOKEN_WEIGHT), ("vector", vector_ranked, VECTOR_WEIGHT)):
        for rank, pk in enumerate(ranked, start=1):
            scores[pk] = scores.get(pk, 0.0) + weight / (RRF_K + rank)
            if rank < best_rank.get(pk, 10**9):
                best_rank[pk] = rank
            if channel == "vector":
                vector_rank[pk] = rank
    ordered = sorted(
        scores.items(),
        key=lambda item: (-item[1], best_rank.get(item[0], 10**9), vector_rank.get(item[0], 10**9), str(item[0])),
    )
    return [(score, pk) for pk, score in ordered[: max(1, top_k)]]


# ------------------------------------------------------------------ 索引（增量缓存）


def _load_meta_rows():
    from ai.models.ai import AiKnowledgeChunk

    return list(
        AiKnowledgeChunk.objects.exclude(embedding__isnull=True)
        .order_by("source_path", "chunk_index")
        .values_list("pk", "content_hash", "embedding_hash", "embedding_model", "embedding_dim")
    )


def vector_index():
    """pk → VectorEntry（增量刷新后返回浅拷贝）；不可用时返回 None。

    可用性判据：存在 embedding 档案 + 未超容量 + 索引条数达到最小可用条数。
    只纳管「新鲜」向量（``embedding_hash == content_hash`` 且模型与当前档案一致），
    陈旧向量不参与向量通道（避免用旧正文的语义召回当前问题）。

    元数据行经 ``index_meta`` 短 TTL 缓存（同 scope 的签名比对在窗口内复用），
    构建/写入路径会显式清缓存（见 ``invalidate_vector_index``）。
    """
    from ai.models.ai import AiKnowledgeChunk
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
                "knowledge vector index disabled: %s vectors exceed capacity %s", len(rows), MAX_INDEXED_VECTORS
            )
            _OVERFLOW_WARNED = True
        return None

    usable = [
        (pk, content_hash, int(dim or 0))
        for pk, content_hash, embedding_hash, embedding_model, dim in rows
        if embedding_hash == content_hash and embedding_model == model
    ]
    if len(usable) < MIN_INDEXED_VECTORS:
        return None

    with _LOCK:
        signatures = {pk: f"{content_hash}:{dim}:{model}" for pk, content_hash, dim in usable}
        changed = [
            pk
            for pk, signature in signatures.items()
            if (entry := _INDEX.get(pk)) is None or entry.signature != signature
        ]
        for pk in [pk for pk in _INDEX if pk not in signatures]:
            _INDEX.pop(pk, None)
        # 向量字节只在签名变化时回读（避免每次检索搬运全量二进制）
        for start in range(0, len(changed), LOAD_BATCH_SIZE):
            batch = changed[start : start + LOAD_BATCH_SIZE]
            for pk, blob in AiKnowledgeChunk.objects.filter(pk__in=batch).values_list("pk", "embedding"):
                entry = _build_entry(signatures[pk], blob)
                if entry is None:
                    _INDEX.pop(pk, None)
                else:
                    _INDEX[pk] = entry
        return {pk: _INDEX[pk] for pk in signatures if pk in _INDEX}


def _build_entry(signature: str, blob):
    """二进制向量 → 索引条目（长度非法/零向量返回 None）。"""
    decoded = decode_vector(blob)
    if decoded is None:
        return None
    norm = math.sqrt(sum(value * value for value in decoded))
    if norm <= 0:
        return None
    return VectorEntry(signature=signature, norm=norm, vector=decoded)


def invalidate_vector_index() -> None:
    """清空向量索引与查询缓存（构建完成后调用；签名比对本身也能发现变化）。

    同时清本进程的元数据签名缓存：构建写库后无需等短 TTL 即可检索到新向量。
    """
    from ai.utils.index_meta import SCOPE_VECTOR_META, invalidate_index_meta

    with _LOCK:
        _INDEX.clear()
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
    """向量通道排名（pk 列表，余弦降序）；任何不可用情形返回空列表。"""
    from ai.utils.ai_config import embedding_credentials

    credentials = embedding_credentials()
    if credentials is None:
        return []
    index = vector_index()
    if not index:
        return []
    from common.sdk.ai.embeddings import EmbeddingClient

    client = EmbeddingClient(credentials)
    query_vector = _embed_query(client, question)
    if query_vector is None:
        return []
    scored = []
    for pk, entry in index.items():
        if len(entry.vector) != len(query_vector):
            continue
        similarity = cosine_similarity(query_vector, entry.vector)
        scored.append((similarity, pk))
    scored.sort(key=lambda item: (-item[0], str(item[1])))
    return [pk for _score, pk in scored[: max(1, top_k)]]


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
      ``ok=False`` + ``failed`` 计数返回，调用方据此给出可读提示。
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
                )
            )
        AiKnowledgeChunk.objects.bulk_update(
            rows, ["embedding", "embedding_model", "embedding_hash", "embedding_dim"], batch_size=batch_size
        )
        summary["embedded"] += len(rows)
        _report(summary["embedded"], stage="embed")
        usage_total = _accumulate_usage(usage_total, client.last_usage)
    summary["dim"] = dim
    invalidate_vector_index()
    if summary["embedded"]:
        _record_build_usage(usage_total, model, started, summary)
    return summary


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
    from django.db.models import F

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
