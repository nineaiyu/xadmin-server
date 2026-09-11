#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""可重入分布式锁（借鉴 jumpserver DistributedLock：可重入 + 自动续期 + 事务提交后释放）。

在 redis-py 原生锁（token 身份 + Lua 原子释放/续期）之上补齐三个能力：

- 可重入：同线程重复 acquire 只计数，重入不延长过期时间；计数归零才真释放；
- 看门狗自动续期：持锁期间按 timeout/3 周期 extend，长任务（如批量导入汇总）
  不再因锁先于业务超时失效而失去互斥；线程结束/进程退出看门狗随之消亡；
- 事务提交后释放：``release_on_commit=True`` 时经 transaction.on_commit 延迟真释放，
  避免事务内持锁、提交前释放导致并发请求在提交窗口内读到未提交态。

非目标（登记边界）：不跨线程重入（token 挂 thread-local）；不解决「看门狗线程被
杀后锁过期」的语义（redis 侧 TTL 兜底释放）。阻塞获取与 django cache.lock 的
blocking_timeout 语义一致。
"""

import threading
import uuid

from django.db import transaction
from django_redis import get_redis_connection

from common.utils import get_logger

logger = get_logger(__name__)

LOCK_KEY_PREFIX = "xadmin:lock:"


class LockNotOwnedError(Exception):
    """未持锁却触发释放/续期（并发过期后锁被他人持有）。"""


class ReentrantLock:
    """按 name 隔离的可重入分布式锁。"""

    def __init__(self, name, timeout=60, blocking_timeout=None, release_on_commit=False):
        self.name = f"{LOCK_KEY_PREFIX}{name}"
        # 过期时间下限保护：看门狗按 timeout/3 续期，过短的 timeout 会造成续期风暴
        self.timeout = max(int(timeout), 5)
        self.blocking_timeout = blocking_timeout
        self.release_on_commit = release_on_commit
        self._local = threading.local()

    def _conn(self):
        return get_redis_connection("default")

    @property
    def _held(self):
        return getattr(self._local, "count", 0) > 0

    def acquire(self, blocking=True) -> bool:
        """获取锁；同线程重入直接计数成功。返回 False 表示竞争失败。"""
        if self._held:
            self._local.count += 1
            return True
        token = uuid.uuid4().hex
        # thread_local=False：token 挂锁实例而非线程（看门狗线程才能 extend）
        lock = self._conn().lock(
            self.name, timeout=self.timeout, blocking_timeout=self.blocking_timeout, thread_local=False
        )
        if not lock.acquire(blocking=blocking, token=token):
            return False
        self._local.lock = lock
        self._local.count = 1
        self._start_watchdog()
        return True

    def release(self):
        """释放一层持有；重入计数归零才真释放（release_on_commit 时延迟到事务提交后）。"""
        if not self._held:
            return
        self._local.count -= 1
        if self._held:
            return
        self._stop_watchdog()
        lock = self._local.lock
        self._local.lock = None

        def _release():
            try:
                lock.release()
            except Exception as exc:
                # 锁已过期并被他人持有（redis-py LockNotOwnedError）或连接抖动：
                # 释放语义已达成（不再持有），仅记录，不向业务抛错
                logger.warning("reentrant lock release skipped: %s (%s)", self.name, exc)

        if self.release_on_commit:
            transaction.on_commit(_release)
        else:
            _release()

    def _start_watchdog(self):
        # 看门狗运行在独立线程：锁引用经闭包捕获（thread-local 跨线程不可见）
        lock = self._local.lock
        stop = threading.Event()
        self._local.watchdog_stop = stop
        interval = max(self.timeout / 3, 1)

        def _watch():
            while not stop.wait(interval):
                try:
                    # extend 校验 token（Lua 原子）：锁易主后续期失败，看门狗退出
                    lock.extend(self.timeout)
                except Exception as exc:
                    logger.warning("reentrant lock watchdog exit: %s (%s)", self.name, exc)
                    return

        threading.Thread(target=_watch, name=f"lock-watchdog:{self.name}", daemon=True).start()

    def _stop_watchdog(self):
        stop = getattr(self._local, "watchdog_stop", None)
        if stop is not None:
            stop.set()

    def __enter__(self):
        if not self.acquire():
            raise LockNotOwnedError(f"acquire lock failed: {self.name}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False
