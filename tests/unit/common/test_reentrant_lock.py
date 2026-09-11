# -*- coding: utf-8 -*-
"""可重入分布式锁：重入计数 / 跨线程互斥 / 看门狗续期 / 事务提交后释放。"""

import threading
import time

import pytest
from django.db import transaction

from common.cache.lock import LockNotOwnedError, ReentrantLock

pytestmark = pytest.mark.django_db


class TestReentrantLock:
    def test_acquire_release_roundtrip(self):
        lock = ReentrantLock("test:roundtrip", timeout=10)
        assert lock.acquire(blocking=False) is True
        lock.release()
        assert lock.acquire(blocking=False) is True
        lock.release()

    def test_reentrant_same_thread(self):
        lock = ReentrantLock("test:reentry", timeout=10)
        with lock:
            with lock:  # 同线程重入：计数，不自我阻塞
                pass
            # 内层已归零释放……外层仍持锁吗？——外层 with 已结束，此处锁应可被重新获取
        assert lock.acquire(blocking=False) is True
        lock.release()

    def test_reentrant_counts_nested(self):
        lock = ReentrantLock("test:nested", timeout=10)
        lock.acquire(blocking=False)
        lock.acquire(blocking=False)
        lock.release()  # 计数 2→1：仍持锁
        other = ReentrantLock("test:nested", timeout=10)
        assert other.acquire(blocking=False) is False
        lock.release()  # 计数 1→0：真释放
        assert other.acquire(blocking=False) is True

    def test_cross_thread_mutex(self):
        lock = ReentrantLock("test:mutex", timeout=30)
        lock.acquire(blocking=False)
        results = []

        def _try():
            other = ReentrantLock("test:mutex", timeout=30)
            results.append(other.acquire(blocking=False))

        t = threading.Thread(target=_try)
        t.start()
        t.join()
        lock.release()
        assert results == [False]

    def test_blocking_acquire_succeeds_after_release(self):
        """阻塞获取在持锁线程释放后成功（release 必须在持锁线程，计数按线程隔离）。"""
        lock = ReentrantLock("test:blocking", timeout=30)
        assert lock.acquire(blocking=False) is True
        results = []

        def _blocking_acquirer():
            other = ReentrantLock("test:blocking", timeout=30, blocking_timeout=5)
            got = other.acquire(blocking=True)
            results.append(got)
            if got:
                other.release()

        t = threading.Thread(target=_blocking_acquirer)
        t.start()
        time.sleep(0.2)
        lock.release()
        t.join()
        assert results == [True]

    def test_with_statement_releases_on_error(self):
        lock = ReentrantLock("test:ctx", timeout=10)
        with pytest.raises(RuntimeError):
            with lock:
                raise RuntimeError("boom")
        assert lock.acquire(blocking=False) is True
        lock.release()

    def test_watchdog_extends_lock(self):
        """看门狗续期：持锁时间超过 timeout 后锁仍在（长任务不逾期）。"""
        lock = ReentrantLock("test:watchdog", timeout=5)
        assert lock.acquire(blocking=False) is True
        try:
            time.sleep(6)  # timeout=5 → 看门狗在 5/3s、10/3s 两次续期
            other = ReentrantLock("test:watchdog", timeout=5)
            assert other.acquire(blocking=False) is False
        finally:
            lock.release()

    def test_release_on_commit(self, transactional_db):
        """release_on_commit：事务内释放不生效，提交后锁才可被他人获取。"""
        lock = ReentrantLock("test:commit", timeout=30, release_on_commit=True)
        other = ReentrantLock("test:commit", timeout=30)
        with transaction.atomic():
            assert lock.acquire(blocking=False) is True
            lock.release()
            # 事务未提交：锁仍被持有
            assert other.acquire(blocking=False) is False
        # 提交后：on_commit 回调执行真释放
        assert other.acquire(blocking=False) is True

    def test_immediate_release_without_transaction(self):
        lock = ReentrantLock("test:now", timeout=30)
        lock.acquire(blocking=False)
        lock.release()
        other = ReentrantLock("test:now", timeout=30)
        assert other.acquire(blocking=False) is True

    def test_enter_raises_when_contended(self):
        lock = ReentrantLock("test:enter", timeout=30)
        assert lock.acquire(blocking=False) is True
        try:
            # with 语义 = 阻塞获取；竞争场景必须给 blocking_timeout，否则会等 TTL 过期
            with pytest.raises(LockNotOwnedError):
                with ReentrantLock("test:enter", timeout=30, blocking_timeout=1):
                    pass
        finally:
            lock.release()
