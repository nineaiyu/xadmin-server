#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库索引元数据短 TTL 缓存：检索链路的签名比对不再每次全表拉元数据。

背景：向量索引与分词索引的增量刷新都以「全库块的元数据签名比对」为入口
（``pk/content_hash/embedding_*`` 或 ``pk/content_hash/title``），每次提问都会
把全库行（2 万块上限）拉成 Python 列表——同一进程在 TTL 窗口内复用一份即可。

约束：
- 只缓存元数据行（不含正文与向量字节），内存占用小；
- 块写入路径（``rebuild_chunks`` / ``remove_chunks`` / 索引失效入口）显式清缓存，
  本进程立即生效；多 worker 下由 TTL 兜底（与内存索引本身的进程内语义一致）；
- loader 异常不写缓存（异常向上抛，调用方既有兜底不变）。
"""

import threading
import time
from typing import Any

#: 短 TTL：多 worker 各自缓存，块写入后最迟 TTL 秒被下次签名比对发现
META_CACHE_TTL_SECONDS = 5

#: 两个索引各自的元数据行 scope（字段不同：向量含模型/维度，分词含标题）
SCOPE_CHUNK_META = "chunk_meta"
SCOPE_VECTOR_META = "vector_meta"

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {}  # scope -> (expires_at, rows)


def cached_meta_rows(scope: str, loader: Any) -> Any:
    """按 scope 缓存的元数据行列表：TTL 内命中即返回，否则调用 loader 重取。

    返回列表为共享只读对象（调用方只做遍历与签名比对，不得原地修改）。
    """
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(scope)
        if cached is not None and cached[0] > now:
            return cached[1]
    rows = loader()
    with _LOCK:
        _CACHE[scope] = (time.monotonic() + META_CACHE_TTL_SECONDS, rows)
    return rows


def invalidate_index_meta(*scopes: str) -> None:
    """清元数据缓存：不给 scope 清全部（块集合变化的写入路径调用）。"""
    with _LOCK:
        if not scopes:
            _CACHE.clear()
            return
        for scope in scopes:
            _CACHE.pop(scope, None)
