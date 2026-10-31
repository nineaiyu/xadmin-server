#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 全局并发流式配额：Redis 计数信号量（自 ai_usage 拆分，行为不变）。

流式每条独占请求线程直至模型超时，无上限时高并发会耗尽线程拖垮 HTTP 面；
超限由调用方给可读提示（不静默排队）。
"""

import time
from typing import Any

from django.core.cache import cache

from ai.services.assistant.ai_quota import quota_limits
from common.utils import get_logger

logger = get_logger(__name__)

# 并发流式计数键（进程间共享；带 TTL 兜底，避免异常退出后的计数泄漏永久化）
STREAM_SLOT_KEY = "ai_stream_slots"
STREAM_SLOT_TTL = 1800
# 流式进行中的续期间隔（秒）：长会话每帧都续期会放大缓存往返，按时间窗节流；
# 取值远小于 TTL，单次续期失败仍有多次重试窗口
STREAM_SLOT_REFRESH_INTERVAL = 60


def stream_slots_in_use() -> int:
    try:
        return max(0, int(cache.get(STREAM_SLOT_KEY) or 0))
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
    """原子回退一次占用（计数键过期/缺失时先补零，避免 DECR 得到负数）。

    长会话跨过 TTL 边界时计数可能已被清零；补 ``add`` 再 ``decr`` 并夹到 0，
    比「先 GET 再按 >0 才 DECR」少一次竞态窗口（并发释放不会把计数减穿）。
    """
    try:
        cache.add(STREAM_SLOT_KEY, 0, STREAM_SLOT_TTL)
        if int(cache.decr(STREAM_SLOT_KEY)) < 0:
            cache.set(STREAM_SLOT_KEY, 0, STREAM_SLOT_TTL)
    except Exception:  # noqa: BLE001
        logger.debug("release AI stream slot failed", exc_info=True)


def refresh_stream_slot() -> None:
    """流式进行中的续期心跳：防止长会话跨过 TTL 后计数被清零（上限被稀释）。

    ``touch`` 对已过期的键返回 False 且不会重建；此时先 ``add`` 占位再把本槽
    计回计数（并发心跳中只有一个 ``add`` 成功，其余 ``incr`` 各自计回自己的槽），
    使过期边界上的在途流重新被计入，而不是静默丢失。
    """
    try:
        if cache.touch(STREAM_SLOT_KEY, STREAM_SLOT_TTL):
            return
        cache.add(STREAM_SLOT_KEY, 0, STREAM_SLOT_TTL)
        cache.incr(STREAM_SLOT_KEY)
    except Exception:  # noqa: BLE001 心跳失败不阻断流式（观测面 fail-open）
        logger.debug("refresh AI stream slot failed", exc_info=True)


class StreamSlot:
    """并发流式配额上下文（``with stream_slot() as ok:``）：异常路径也保证释放。"""

    def __init__(self) -> None:
        self.acquired = False
        self._last_refresh = 0.0

    def __enter__(self) -> bool:
        self.acquired = acquire_stream_slot()
        self._last_refresh = time.monotonic()
        return self.acquired

    def heartbeat(self) -> None:
        """按时间窗续期（每个流式事件调用，内部节流到 ``STREAM_SLOT_REFRESH_INTERVAL``）。"""
        if not self.acquired:
            return
        now = time.monotonic()
        if now - self._last_refresh < STREAM_SLOT_REFRESH_INTERVAL:
            return
        self._last_refresh = now
        refresh_stream_slot()

    def __exit__(self, *exc_info: Any) -> Any:
        if self.acquired:
            release_stream_slot()
        return False


def stream_slot() -> StreamSlot:
    return StreamSlot()
