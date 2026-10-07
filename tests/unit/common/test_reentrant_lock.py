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
        """看门狗续期：持锁期间 TTL 被看门狗周期性重置（长任务不逾期）。

        不用「睡过 timeout 后锁仍在」的墙钟推断：xdist 高并发负载下看门狗线程可能
        被调度延迟，墙钟断言偶发误判为逾期。改为直接观测续期事件——TTL 在两次采样
        间只会衰减（int 秒粒度下不回升），任何回升都只能是看门狗 extend 重置所致，
        与线程调度快慢无关；观测到续期后锁必然仍在持有期，互斥断言随之确定性成立。
        锁名带唯一后缀：与其它轮次/并行的同用例实例互不共享 Redis 键，消除残留干扰。
        """
        from uuid import uuid4

        from django_redis import get_redis_connection

        name = f"test:watchdog:{uuid4().hex}"
        lock = ReentrantLock(name, timeout=5)
        conn = get_redis_connection("default")
        assert lock.acquire(blocking=False) is True
        try:
            last_ttl = conn.ttl(lock.name)
            assert last_ttl > 0, f"锁键 TTL 异常：{last_ttl}"
            deadline = time.monotonic() + 30  # 首次续期在 ~timeout/3s；30s 容忍极端调度延迟
            extended = False
            while time.monotonic() < deadline:
                time.sleep(0.05)
                ttl = conn.ttl(lock.name)
                if ttl > last_ttl:
                    extended = True
                    break
                last_ttl = ttl
            assert extended, "看门狗未在窗口内重置锁 TTL（续期事件未观测到）"
            other = ReentrantLock(name, timeout=5)
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
