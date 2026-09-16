# -*- coding: utf-8 -*-
"""common/decorators.py：单例、内存缓存、延迟防抖与合并参数装饰器。"""

import asyncio
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]

from common.decorators import (
    Debouncer,
    EventLoopThread,
    Singleton,
    cached_method,
    cancel_or_remove_debouncer_task,
    default_suffix_key,
    delay_run,
    merge_delay_run,
)


def wait_until(predicate, timeout=5.0, interval=0.05):
    """轮询等待后台防抖任务落盘，替代固定 sleep（负载高时盲等会被打穿）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    return predicate()


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

    # django_db 必须保留：任务经 open_db_connection 在后台线程自建连接，
    # pytest-django 的 DB 屏蔽是进程级（含非主线程），无标记时任务会被拦截丢弃
    @pytest.mark.django_db
    def test_delayed_execution_runs_once(self):
        calls = []

        @delay_run(ttl=0.2)
        def job():
            calls.append(1)

        job()
        job()
        job()
        # 防抖语义：三次调用合并为一次执行，轮询到首次执行即终态（不存在二次触发）
        assert wait_until(lambda: calls) == [1]


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
        assert wait_until(lambda: seen) == [{"a", "b"}]

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
        from common.decorators import debounce

        loop = debounce.get_loop()
        assert loop is not None
        assert loop.is_running()
        # 惰性创建入口：get_loop() 之后线程已就绪且为事件循环线程实例
        assert isinstance(debounce._loop_thread, EventLoopThread)
        assert debounce._loop_thread.is_alive()

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
        # 轮询到执行完成；防抖的「延迟语义」仍以 elapsed 下限断言兜底
        assert wait_until(lambda: calls) == [1]
        assert time.time() - start >= 0.1


class TestLazyInitialization:
    """事件循环线程与执行池惰性创建：import 不得产生后台线程/线程池副作用。

    回归背景：原实现 `common/decorators.py` 在 import 即启动一条事件循环线程 +
    创建 10 线程执行池，所有间接 import 方（celery/worker 等）都常驻这些资源。
    """

    def test_fresh_import_has_no_background_resources(self):
        """全新解释器 import 后：无事件循环线程、无执行池。"""
        code = textwrap.dedent(
            """
            import threading

            import common.decorators  # noqa: F401
            from common.decorators import debounce

            assert debounce._loop_thread is None, "import 即创建了事件循环线程"
            assert debounce._executor is None, "import 即创建了线程池"
            assert not any(isinstance(t, debounce.EventLoopThread) for t in threading.enumerate())
            print("lazy-ok")
            """
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            env={**os.environ, "DJANGO_SETTINGS_MODULE": "tests.settings_test"},
            timeout=120,
        )
        assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
        assert "lazy-ok" in proc.stdout

    def test_executor_created_on_demand(self):
        """历史模块级 `executor` 引用兼容：访问即惰性创建，且与 get_executor() 同实例。"""
        from common import decorators
        from common.decorators import debounce

        assert decorators.executor is debounce.get_executor()
