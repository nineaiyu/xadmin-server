#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库块级分词缓存：检索加速（不改变检索结果）。

背景：词频检索原先每次问答都对全库块重新分词（O(块数 × 块长) 的 CPU 热点，
ADR-037 实测 535 块 / 平均 30ms）。本模块缓存每块的「去重 token 集合 + 未去重
token 总数 + 标题 token 集合」，以 ``content_hash + title`` 为签名增量维护
（新增 / 修改 / 删除块自动重建），检索侧只做集合查找。

约束：
- 结果一致性：命中判定与长度归一使用与历史实现相同的分词口径（``_tokenize``：
  CJK 二元组 + ASCII 词）；条目顺序按 (source_path, chunk_index)（与模型
  Meta.ordering 一致），同分块的相对顺序不变；
- 内存：token 经 ``sys.intern`` 去重（不同块的相同词元共享同一字符串对象）；
  块数超过 ``MAX_INDEXED_CHUNKS`` 时返回 None，调用方退回逐块分词（不缓存）。
"""

import re
import sys
import threading
from typing import NamedTuple

from common.utils import get_logger

logger = get_logger(__name__)

# 缓存容量上限：超过即停用缓存（退回逐块分词），避免异常规模下内存失控
MAX_INDEXED_CHUNKS = 20000
# 变更块批量回填分片大小（避免超长 IN 查询）
LOAD_BATCH_SIZE = 200

_LOCK = threading.Lock()
_INDEX: dict = {}
_OVERFLOW_WARNED = False


class ChunkTokens(NamedTuple):
    """单块分词结果（token 集合为 interned 字符串，可安全跨线程共享）。"""

    signature: str
    total_tokens: int  # 未去重 token 总数（评分长度归一）
    tokens: frozenset  # 去重 token 集合（命中判定）
    title_tokens: frozenset


def _tokenize(text: str) -> list:
    """CJK 二元组 + ASCII 词。"""
    tokens = []
    for word in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        if re.fullmatch(r"[a-z0-9_]+", word):
            tokens.append(word)
        else:
            tokens.extend(word[i : i + 2] for i in range(len(word) - 1))
    return tokens


def _intern(tokens) -> frozenset:
    return frozenset(sys.intern(token) for token in tokens)


def _build_entry(signature: str, content: str, title: str) -> ChunkTokens:
    tokens = _tokenize(content or "")
    return ChunkTokens(
        signature=signature,
        total_tokens=len(tokens),
        tokens=_intern(tokens),
        title_tokens=_intern(_tokenize(title or "")),
    )


def chunk_token_index():
    """块 pk → ChunkTokens（增量刷新后返回浅拷贝）；超容量停用缓存时返回 None。

    每次调用做一次轻量签名比对（pk + content_hash + title，按模型 ordering），
    仅变化块回读全文重新分词；块删除自动清理。
    """
    from ai.models.ai import AiKnowledgeChunk

    global _OVERFLOW_WARNED

    rows = list(
        AiKnowledgeChunk.objects.order_by("source_path", "chunk_index").values_list("pk", "content_hash", "title")
    )
    if len(rows) > MAX_INDEXED_CHUNKS:
        if not _OVERFLOW_WARNED:
            logger.warning(
                "knowledge chunk index disabled: %s chunks exceed capacity %s", len(rows), MAX_INDEXED_CHUNKS
            )
            _OVERFLOW_WARNED = True
        return None

    with _LOCK:
        signatures = {pk: f"{content_hash}:{title or ''}" for pk, content_hash, title in rows}
        changed = [
            pk
            for pk, signature in signatures.items()
            if (entry := _INDEX.get(pk)) is None or entry.signature != signature
        ]
        for pk in [pk for pk in _INDEX if pk not in signatures]:
            _INDEX.pop(pk, None)
        for start in range(0, len(changed), LOAD_BATCH_SIZE):
            batch = changed[start : start + LOAD_BATCH_SIZE]
            for pk, content, title in AiKnowledgeChunk.objects.filter(pk__in=batch).values_list(
                "pk", "content", "title"
            ):
                _INDEX[pk] = _build_entry(signatures[pk], content, title)
        # 返回按模型 ordering 排列的浅拷贝：检索侧遍历顺序 = 历史实现的 DB 顺序，
        # 同分块在稳定排序下的相对次序不变
        return {pk: _INDEX[pk] for pk, _hash, _title in rows if pk in _INDEX}


def invalidate_chunk_index() -> None:
    """清空缓存（写入路径可选调用；签名比对本身能发现变化，此入口供测试/运维）。"""
    with _LOCK:
        _INDEX.clear()
