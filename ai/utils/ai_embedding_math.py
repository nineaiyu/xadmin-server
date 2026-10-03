#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库向量检索：编解码 / 余弦相似度 / RRF 融合（自 ai_embeddings 拆分，行为不变）。

纯函数（无数据库、无外部服务）：落库二进制形态、相似度与排名融合口径见
docs/adr 检索设计档案；向量通道不可用时由调用方回退词频结果。
"""

import array
import math

#: RRF 融合常数（Cormack 等 2009 原文取值；排名越靠前权重越高）
RRF_K = 60
#: 通道权重：词频是质量基线（实测 hit@5 97.2%），向量为语义补充——
#: 单通道向量命中排在词频命中之后（弱 embedding 模型不会稀释基线质量），
#: 词频无命中/命中稀少时（改写式提问、同义表述）由向量通道补齐 top-k
TOKEN_WEIGHT = 1.0
VECTOR_WEIGHT = 0.5


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
