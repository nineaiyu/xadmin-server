#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库 embedding 构建的运行状态通道（缓存，下载中心进度同思路）。

为什么走缓存而不是库表：构建进度是**高频短生命周期**状态（每批一次更新、
终态保留 1 小时自愈），为此建表过重；且下载中心 ExportRecord 的语义是「产物
文件 + 下载」，embedding 构建没有产物，只承载进度。全局单飞锁同样落缓存
（``cache.add`` NX），防止并发构建重复消耗 embedding API 预算。

状态结构（``BUILD_STATUS_KEY``，TTL 1 小时）::

    {"state": "running|done|error", "percent": 0-100, "stage": 阶段描述,
     "embedded": 已构建块数, "total": 待构建块数, "summary": 终态摘要, ...}
"""

from django.core.cache import cache
from django.utils import timezone

#: 全局单飞锁（cache.add NX；worker 崩溃由 TTL 自愈，不永久卡死构建入口）
BUILD_LOCK_KEY = "ai_embedding_build_lock"
BUILD_LOCK_TTL = 1800
#: 运行状态（含终态摘要；1 小时后自愈消失，前端按 state 展示）
BUILD_STATUS_KEY = "ai_embedding_build_status"
STATUS_TTL = 3600


def try_acquire_lock() -> bool:
    """获取构建单飞锁（已有人在跑返回 False）。"""
    return bool(cache.add(BUILD_LOCK_KEY, "1", BUILD_LOCK_TTL))


def release_lock() -> None:
    cache.delete(BUILD_LOCK_KEY)


def _write(values: dict) -> None:
    current = cache.get(BUILD_STATUS_KEY) or {}
    current.update(values)
    current["updated_time"] = timezone.now().isoformat()
    cache.set(BUILD_STATUS_KEY, current, STATUS_TTL)


def get_status() -> dict:
    return cache.get(BUILD_STATUS_KEY) or {"state": "idle", "percent": 0}


def mark_running(total: int) -> None:
    _write({"state": "running", "percent": 0, "stage": "collect", "embedded": 0, "total": int(total or 0)})


def mark_progress(percent: int, stage: str = "", embedded: int = 0) -> None:
    values: dict = {"percent": max(0, min(100, int(percent)))}
    if stage:
        values["stage"] = stage
    if embedded:
        values["embedded"] = int(embedded)
    _write(values)


def mark_finished(summary: dict, ok: bool) -> None:
    """终态：state 落 done/error，完整摘要随状态保留（前端轮询终态后停止）。"""
    _write(
        {
            "state": "done" if ok else "error",
            "percent": 100,
            "stage": "",
            "finished_time": timezone.now().isoformat(),
            "summary": summary or {},
        }
    )


def progress_callback():
    """构建进度回调（供 build_embeddings 的 ``progress_cb`` 钩子直接使用）。"""

    def _cb(percent: int, stage: str = "", embedded: int = 0) -> None:
        mark_progress(percent, stage=stage, embedded=embedded)

    return _cb
