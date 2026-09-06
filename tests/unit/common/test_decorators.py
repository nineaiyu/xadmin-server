# -*- coding: utf-8 -*-
"""common/decorators.py：单例、内存缓存、延迟防抖与合并参数装饰器。"""
import asyncio
import time

import pytest

from common.decorators import (
    Debouncer,
    EventLoopThread,
    Singleton,
    cancel_or_remove_debouncer_task,
    cached_method,
    default_suffix_key,
    delay_run,
    merge_delay_run,
)


class TestSingleton:
    def test_same_instance_returned(self):
        @Singleton
        class Foo:
            def __init__(self):
                self.value = object()

        assert Foo() is Foo()


def test_default_suffix_key():
    assert default_suffix_key(1, 2, other=3) == "default"


class TestCachedMethod:
    def test_cached_result_reused(self):
        calls = []

        @cached_method(ttl=10)
        def compute(x, y=0):
            calls.append((x, y))
            return x + y

        assert compute(1, y=2) == 3
        assert compute(1, y=2) == 3
        assert compute(2) == 2
        assert calls == [(1, 2), (2, 0)]

    def test_ttl_expiry(self):
        calls = []

        @cached_method(ttl=0.05)
        def compute():
            calls.append(1)
            return len(calls)

        assert compute() == 1
        assert compute() == 1  # ttl 内命中缓存
        time.sleep(0.06)
        assert compute() == 2  # 过期后重算

    def test_negative_ttl_is_permanent(self):
        calls = []

        @cached_method(ttl=-1)
        def compute():
            calls.append(1)
            return "v"

        compute()
        time.sleep(0.01)
        assert compute() == "v"
        assert calls == [1]


class TestDelayRun:
    def test_requires_zero_args(self):
        with pytest.raises(ValueError):
            @delay_run(ttl=1)
            def bad(a):  # noqa
                pass

    @pytest.mark.django_db
    def test_delayed_execution_runs_once(self):
        calls = []

        @delay_run(ttl=0.2)
        def job():
            calls.append(1)

        job()
        job()
        job()
        time.sleep(0.6)
        assert calls == [1]


class TestMergeDelayRun:
    def test_requires_one_tuple_default_arg(self):
        with pytest.raises(ValueError):
            @merge_delay_run(ttl=1)
            def no_args():  # noqa
                pass

        with pytest.raises(ValueError):
            @merge_delay_run(ttl=1)
            def bad_default(users=[]):  # noqa
                pass

    @pytest.mark.django_db
    def test_delay_merges_kwargs_across_calls(self):
        seen = []

        @merge_delay_run(ttl=0.3, key=lambda *a, **k: "g")
        def job(users=()):
            seen.append(set(users))

        # delay 经 partial 绑定 func，只传业务参数
        job.delay(users=["a"])
        job.delay(users=["b"])
        time.sleep(0.8)
        assert seen == [{"a", "b"}]

    @pytest.mark.django_db
    def test_delay_rejects_scalar_kwargs(self):
        @merge_delay_run(ttl=1, key=lambda *a, **k: "g")
        def job(users=()):
            pass

        with pytest.raises(ValueError):
            job.delay(users="scalar")

    def test_apply_sync_runs_immediately(self):
        seen = []

        @merge_delay_run(ttl=1, key=lambda *a, **k: "g")
        def job(users=()):
            seen.append(list(users))

        # apply 经 functools.partial 绑定了 func，无需再传
        job.apply(sync=True, users=["x"])
        assert seen == [["x"]]


class TestDebounceInfrastructure:
    def test_cancel_or_remove_unknown_key_is_noop(self):
        assert cancel_or_remove_debouncer_task("NO_SUCH_KEY") is None

    def test_event_loop_thread_running(self):
        from common import decorators

        loop = decorators.get_loop()
        assert loop is not None
        assert loop.is_running()
        assert isinstance(decorators._loop_thread, EventLoopThread)
        assert decorators._loop_thread.is_alive()

    def test_debouncer_awaits_delay_then_calls_back(self):
        loop = asyncio.new_event_loop()
        results = []
        debouncer = Debouncer(
            lambda *a: results.append(a),
            lambda: True,
            0.05,
            loop=loop,
        )
        task = loop.create_task(debouncer("arg"))
        loop.run_until_complete(task)
        loop.close()
        assert results == [("arg",)]

    @pytest.mark.django_db
    def test_run_debouncer_func_executes_immediately_after_ttl(self):
        calls = []

        @delay_run(ttl=0.1)
        def job():
            calls.append(1)

        start = time.time()
        job()
        time.sleep(0.5)
        assert calls == [1]
        assert time.time() - start >= 0.1
