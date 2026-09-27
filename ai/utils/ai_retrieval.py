#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库检索：词频评分（基线）+ 向量通道 RRF 混合（可选，见 ADR-065）。

口径与历史实现一致：CJK 二元组 + ASCII 词、命中数（去重词元）+ 标题加成、
``boosted / sqrt(token 总数)`` 打分、阈值过滤、score 降序取 top_k。

加速路径：块级分词缓存（``ai_index.chunk_token_index``）只做集合查找，避免每次
问答全库重新分词；缓存不可用（块数超容量/缓存模块异常）时退回逐块分词全扫，
结果不变（守护测试对比两条路径）。

混合路径：配置了 ``purpose=embedding`` 激活档案且存在新鲜向量时，词频排名与向量
排名经 RRF 融合（``ai_embeddings.hybrid_rank``）；**未配置 = 与历史实现逐行等价**，
向量链路异常同样回退词频结果（问答可用性不被向量可用性绑定）。
"""

from ai.utils.ai_index import _tokenize, chunk_token_index
from common.utils import get_logger

logger = get_logger(__name__)

TOP_K = 5
SCORE_THRESHOLD = 2
MAX_QUESTION_LENGTH = 500


def _score_entry(query_tokens: set, total_tokens: int, tokens, title_tokens) -> float:
    """命中数（去重词元）+ 标题加成 → 长度归一分数；未达阈值返回 0。"""
    raw_hits = sum(1 for token in query_tokens if token in tokens)
    title_hit = 1 if query_tokens & title_tokens else 0
    boosted = raw_hits + title_hit
    if boosted < SCORE_THRESHOLD:
        return 0.0
    return boosted / (total_tokens**0.5)


def _load_chunks(pks: list) -> dict:
    from ai.models.ai import AiKnowledgeChunk

    return {
        chunk.pk: chunk
        for chunk in AiKnowledgeChunk.objects.filter(pk__in=pks).only(
            "id", "source_path", "title", "content", "chunk_index"
        )
    }


def _format(scored: list, top_k: int) -> list:
    top = scored[:top_k]
    chunks = _load_chunks([pk for _score, pk in top])
    return [{"chunk": chunks[pk], "score": round(score, 4)} for score, pk in top if pk in chunks]


def retrieve(question: str, top_k: int = TOP_K) -> list:
    """检索，返回 [{chunk 实例, score}]（score 降序，阈值过滤）。

    词频为基线通道；向量通道可用时走 RRF 混合（score 语义为融合分，消费方只用
    ``chunk`` 与顺序，契约不变）。
    """
    question = (question or "").strip()[:MAX_QUESTION_LENGTH]
    if not question:
        return []
    query_tokens = set(_tokenize(question))
    index = chunk_token_index()
    if index is None:
        # 超容量等场景停用缓存：退回逐块分词（与历史实现逐行等价）
        return _retrieve_full_scan(query_tokens, top_k)

    scored = []
    for pk, entry in index.items():
        if not entry.total_tokens:
            continue
        score = _score_entry(query_tokens, entry.total_tokens, entry.tokens, entry.title_tokens)
        if score:
            scored.append((score, pk))
    scored.sort(key=lambda item: item[0], reverse=True)
    fused = _hybrid(scored, question, top_k)
    if fused is not None:
        return _format(fused, top_k)
    return _format(scored, top_k)


def _hybrid(scored: list, question: str, top_k: int):
    """向量通道融合（不可用/异常返回 None，调用方走词频原路径）。"""
    from ai.utils.ai_embeddings import hybrid_rank

    try:
        return hybrid_rank(question, [pk for _score, pk in scored], top_k)
    except Exception as exc:  # noqa: BLE001 混合链路异常一律回退词频（可用性优先）
        logger.warning("ai hybrid retrieval failed, fallback to token ranking: %s", exc)
        return None


def _retrieve_full_scan(query_tokens: set, top_k: int) -> list:
    """无缓存兜底：逐块读全文分词评分（历史实现；仅在缓存停用时走）。"""
    from ai.models.ai import AiKnowledgeChunk

    scored = []
    for chunk in AiKnowledgeChunk.objects.all().only("id", "source_path", "title", "content", "chunk_index"):
        content_tokens = _tokenize(chunk.content)
        if not content_tokens:
            continue
        score = _score_entry(query_tokens, len(content_tokens), set(content_tokens), set(_tokenize(chunk.title)))
        if score:
            scored.append((score, chunk.pk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return _format(scored, top_k)
