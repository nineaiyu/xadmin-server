#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 全局并发流式配额：Redis 计数信号量（自 ai_usage 拆分，行为不变）。

流式每条独占请求线程直至模型超时，无上限时高并发会耗尽线程拖垮 HTTP 面；
超限由调用方给可读提示（不静默排队）。
"""

from django.core.cache import cache

from ai.utils.ai_quota import quota_limits
from common.utils import get_logger

logger = get_logger(__name__)

# 并发流式计数键（进程间共享；带 TTL 兜底，避免异常退出后的计数泄漏永久化）
STREAM_SLOT_KEY = "ai_stream_slots"
STREAM_SLOT_TTL = 1800


def stream_slots_in_use() -> int:
    try:
        return int(cache.get(STREAM_SLOT_KEY) or 0)
    except Exception:  # noqa: BLE001 缓存不可用按无占用兜底（并发准入另有单飞锁）
        return 0


def acquire_stream_slot() -> bool:
    """全局并发流式配额（Redis 计数信号量）：超限返回 False（调用方给可读提示）。

    两步保持「先自增再判定」的原子语义（``INCR`` 自带读改写原子性）：
    超限时立即回退本次自增；成功后对计数键**续期**（``touch``）——
    旧实现只在首次创建时设 TTL，长会话（>STREAM_SLOT_TTL）会让键中途过期、
    计数被清零而失去并发上限（配额静默失效）。

    非 Redis 后端（LocMem/测试后端）对缺失键 ``incr`` 抛 ValueError：
    补一次 ``add`` 占位后重试，语义一致。
    """
    limit = quota_limits()["concurrent_streams"]
    if limit <= 0:
        return True
    try:
        try:
            count = int(cache.incr(STREAM_SLOT_KEY))
        except ValueError:
            cache.add(STREAM_SLOT_KEY, 0, STREAM_SLOT_TTL)
            count = int(cache.incr(STREAM_SLOT_KEY))
        if count > limit:
            release_stream_slot()  # 回退本次自增，计数维持在上限
            return False
        cache.touch(STREAM_SLOT_KEY, STREAM_SLOT_TTL)
        return True
    except Exception:  # noqa: BLE001 缓存异常不阻断业务（观测面 fail-open）
        logger.warning("acquire AI stream slot failed", exc_info=True)
        return True


def release_stream_slot() -> None:
    try:
        current = int(cache.get(STREAM_SLOT_KEY) or 0)
        if current > 0:
            cache.decr(STREAM_SLOT_KEY)
    except Exception:  # noqa: BLE001
        logger.debug("release AI stream slot failed", exc_info=True)


class StreamSlot:
    """并发流式配额上下文（``with stream_slot() as ok:``）：异常路径也保证释放。"""

    def __init__(self):
        self.acquired = False

    def __enter__(self) -> bool:
        self.acquired = acquire_stream_slot()
        return self.acquired

    def __exit__(self, *exc_info):
        if self.acquired:
            release_stream_slot()
        return False


def stream_slot() -> StreamSlot:
    return StreamSlot()
