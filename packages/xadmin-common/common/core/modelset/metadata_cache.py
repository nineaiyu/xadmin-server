#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""元数据载荷缓存：读取 + 单飞重建（与 MagicCacheResponse 同范式）。

缓存窗口到期瞬间的并发未命中只让一个请求回源重建（其余在锁上排队，拿到锁后
二次检查复用首次结果）——元数据接口的冷缓存击穿是 P95 重尾的主因之一
（k6 20 VU 实测 fields 变体 p50 18.6ms / P95 106.7ms，重尾集中在窗口切换点）。

等锁超时（LockError）降级为直接重建：读缓存是优化、不是正确性要求，
不把争用升级为错误；builder 返回 None 表示构建失败，不回写缓存（失败不缓存）。
"""

from django.core.cache import cache
from redis.exceptions import LockError

from common.utils import get_logger

logger = get_logger(__name__)

#: 单飞锁 TTL 与等锁上限（秒）：单次重建约数秒，30s 兜底避免异常残留死锁
METADATA_LOCK_TTL = 30
METADATA_LOCK_WAIT = 35


def cached_payload(cache_key: str, timeout: int, builder, bypass: bool = False):
    """读取载荷缓存；未命中时单飞重建并回写。

    ``bypass=True``（`?no_cache=1`）跳过读写直接重建；``builder()`` 返回 None
    视为构建失败——不回写缓存（避免把失败态缓存成"成功但残缺"）。
    """
    if bypass:
        return builder()
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        with cache.lock(f"locker_{cache_key}", timeout=METADATA_LOCK_TTL, blocking_timeout=METADATA_LOCK_WAIT):
            cached = cache.get(cache_key)  # 等待锁期间可能已有并发请求完成重建
            if cached is not None:
                return cached
            result = builder()
            if result is not None:
                cache.set(cache_key, result, timeout)
            return result
    except LockError:
        logger.warning(f"metadata cache lock timeout, fallback to direct build. key:{cache_key}")
        return builder()
