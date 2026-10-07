#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""仓库文档同步的运行状态通道（缓存，与 embedding 构建进度同思路）。

为什么走缓存而不是库表：同步摘要（created/updated/removed/...）是**短生命周期**
结果状态——任务在 worker 执行，前端只需轮询终态摘要，为此建表过重；终态保留
1 小时自愈。单飞锁同样落缓存（``cache.add`` NX）：并发全量重建会重复扫盘并
互相覆盖分块（先删后插），串行化到一次。

状态结构（``SYNC_STATUS_KEY``，TTL 1 小时）::

    {"state": "running|done|error", "summary": 同步摘要, ...}
"""

from django.core.cache import cache
from django.utils import timezone

#: 全局单飞锁（cache.add NX；worker 崩溃由 TTL 自愈，不永久卡死同步入口）
SYNC_LOCK_KEY = "ai_repo_sync_lock"
SYNC_LOCK_TTL = 1800
#: 运行状态（含终态摘要；1 小时后自愈消失，前端按 state 展示）
SYNC_STATUS_KEY = "ai_repo_sync_status"
STATUS_TTL = 3600


def try_acquire_lock() -> bool:
    """获取同步单飞锁（已有同步在跑返回 False）。"""
    return bool(cache.add(SYNC_LOCK_KEY, "1", SYNC_LOCK_TTL))


def release_lock() -> None:
    cache.delete(SYNC_LOCK_KEY)


def _write(values: dict) -> None:
    current = cache.get(SYNC_STATUS_KEY) or {}
    current.update(values)
    current["updated_time"] = timezone.now().isoformat()
    cache.set(SYNC_STATUS_KEY, current, STATUS_TTL)


def get_status() -> dict:
    return cache.get(SYNC_STATUS_KEY) or {"state": "idle"}


def mark_running() -> None:
    """开工即整体重置为 running：上一轮终态（summary/detail/finished_time）就地清除，
    残留终态不会被下一轮首轮轮询命中；本轮终态摘要仍在结束后随通道保留 1 小时。"""
    cache.set(
        SYNC_STATUS_KEY,
        {"state": "running", "updated_time": timezone.now().isoformat()},
        STATUS_TTL,
    )


def mark_finished(summary: dict, ok: bool, detail: str = "") -> None:
    """终态：state 落 done/error，同步摘要随状态保留（前端轮询终态后停止）。"""
    values = {
        "state": "done" if ok else "error",
        "finished_time": timezone.now().isoformat(),
        "summary": summary or {},
    }
    if detail:
        values["detail"] = detail
    _write(values)
