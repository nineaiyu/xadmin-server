# -*- coding: utf-8 -*-
"""MagicCacheData 缓存基建可靠性测试（PERF-01）。

覆盖三个必须保证的场景：
1. func 抛异常时不缓存空结果，下一次调用重新执行 func，且异常向上传播；
2. 缓存中存在僵尸占位（status='ready'）时能自行计算，不无限阻塞；
3. 并发调用时 func 只执行一次，结果一致。
"""
import threading
import time

import pytest
from django.core.cache import cache

from common.base.magic import MagicCacheData

pytestmark = pytest.mark.django_db

CALLS = "calls"


def _make_counter():
    """返回一个线程安全的调用计数器。"""
    state = {"count": 0}
    lock = threading.Lock()

    def incr():
        with lock:
            state["count"] += 1
            return state["count"]

    return state, incr


class TestMakeCacheException:
    def test_exception_not_cached_and_reraised(self):
        state, incr = _make_counter()

        @MagicCacheData.make_cache(timeout=60, key_func=lambda: "boom")
        def func():
            incr()
            raise ValueError("db hiccup")

        with pytest.raises(ValueError):
            func()
        # 异常时清除占位，缓存中不应留下任何数据
        assert cache.get("magic_cache_data_func_boom") is None

        with pytest.raises(ValueError):
            func()
        # 第二次调用仍真实执行 func（而非命中空缓存返回 ''）
        assert state["count"] == 2

    def test_success_after_exception_is_cached(self):
        state, incr = _make_counter()

        @MagicCacheData.make_cache(timeout=60, key_func=lambda: "recover")
        def func():
            incr()
            if state["count"] == 1:
                raise ValueError("first call failed")
            return "ok"

        with pytest.raises(ValueError):
            func()
        assert func() == "ok"
        assert func() == "ok"  # 命中缓存
        assert state["count"] == 2


class TestMakeCacheZombiePlaceholder:
    def test_ready_placeholder_does_not_block(self):
        state, incr = _make_counter()

        @MagicCacheData.make_cache(timeout=3600, key_func=lambda: "zombie")
        def func():
            incr()
            return f"value-{state['count']}"

        # 模拟计算进程在置 ready 之后崩溃：占位与业务 TTL 无关
        cache.set("magic_cache_data_func_zombie", {"status": "ready", "c_time": time.time()}, 3600)

        start = time.time()
        assert func() == "value-1"
        # 不阻塞在忙等循环上（不存在 0.5s 轮询的等待）
        assert time.time() - start < 5
        assert state["count"] == 1

    def test_placeholder_ttl_is_independent_of_business_ttl(self):
        @MagicCacheData.make_cache(timeout=3600 * 24, key_func=lambda: "long_ttl")
        def func():
            return "data"

        assert func() == "data"
        # 业务 TTL 为 24h，但占位/锁的 TTL 不应超过 PLACEHOLDER_TTL
        assert MagicCacheData.PLACEHOLDER_TTL <= 60


class TestMakeCacheConcurrency:
    def test_concurrent_call_executes_once(self):
        state, incr = _make_counter()
        in_calc = threading.Event()

        @MagicCacheData.make_cache(timeout=60, key_func=lambda: "concurrent")
        def func():
            incr()
            in_calc.set()  # 通知主线程：首个调用方已进入 func
            time.sleep(0.3)  # 拉长计算窗口，让第二个调用方必然阻塞在锁上
            return "shared"

        results = []

        def worker():
            results.append(func())

        first = threading.Thread(target=worker)
        first.start()
        assert in_calc.wait(timeout=5), "首个调用方未进入 func"
        second = threading.Thread(target=worker)
        second.start()
        for t in (first, second):
            t.join(timeout=10)

        assert results == ["shared", "shared"]
        # 分布式锁保证同一时刻只有一个线程进入 func，等待方命中双重检查返回缓存
        assert state["count"] == 1


class TestMakeCacheExpiry:
    def test_cache_hit_avoids_recompute(self):
        state, incr = _make_counter()

        @MagicCacheData.make_cache(timeout=60, key_func=lambda: "hit")
        def func():
            incr()
            time.sleep(0.01)
            return "cached-value"

        assert func() == "cached-value"
        before = time.time()
        assert func() == "cached-value"
        assert time.time() - before < 0.01
        assert state["count"] == 1

    def test_invalid_time_shortens_valid_window(self):
        state, incr = _make_counter()

        @MagicCacheData.make_cache(timeout=10, invalid_time=10, key_func=lambda: "invalid")
        def func():
            incr()
            return "v"

        assert func() == "v"
        assert func() == "v"
        # valid_time 为 0，缓存永不过期命中，每次都重新计算
        assert state["count"] == 2

    def test_invalid_cache_removes_data(self):
        state, incr = _make_counter()

        @MagicCacheData.make_cache(timeout=60, key_func=lambda: "drop")
        def func():
            incr()
            return "v1"

        assert func() == "v1"
        MagicCacheData.invalid_caches(["func_drop"])
        assert func() == "v1"
        assert state["count"] == 2
