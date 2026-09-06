# -*- coding: utf-8 -*-
"""common/utils/pending.py：并发等待队列（pending result）。"""
from common.cache.storage import PendingStateCache
from common.utils.pending import get_pending_result, set_pending_cache


def test_set_pending_cache_removes_key_and_persists():
    cache_obj = PendingStateCache("test-pending-set")
    cache_obj.set_storage_cache(["k1", "k2", "k3"], timeout=60)
    set_pending_cache("k2", ["k1", "k2", "k3"], cache_obj, timeout=60)
    assert cache_obj.get_storage_cache() == ["k1", "k3"]


def test_set_pending_cache_ignores_absent_key():
    cache_obj = PendingStateCache("test-pending-set2")
    set_pending_cache("absent", ["k1"], cache_obj, timeout=60)
    assert cache_obj.get_storage_cache() == ["k1"]


class TestGetPendingResult:
    def test_first_call_executes_and_returns_data(self):
        def func():
            return "payload"

        ok, result = get_pending_result(
            func,
            lambda r: True,
            loop_count=1,
            sleep_time=0.01,
            unique_key="u1",
            locker_key="locker-1",
        )
        assert ok is True
        assert result == {"data": "payload"}

    def test_expect_func_false_times_out(self):
        calls = []

        def func():
            calls.append(1)
            return "no"

        ok, result = get_pending_result(
            func,
            lambda r: False,
            loop_count=1,
            sleep_time=0.01,
            unique_key="u2",
            locker_key="locker-2",
        )
        assert ok is False
        assert result == {"err_msg": "请求超时"}
        assert len(calls) >= 1

    def test_repeated_key_beyond_limit_rejected(self):
        # pop_first=False：超出并发数时移除最新请求，返回重复错误
        cache_obj = PendingStateCache("locker-3")
        cache_obj.set_storage_cache(["u-a"], timeout=60)

        ok, result = get_pending_result(
            lambda: "x",
            lambda r: True,
            loop_count=1,
            sleep_time=0.01,
            unique_key="u-new",
            run_func_count=1,
            pop_first=False,
            locker_key="locker-3",
        )
        assert ok is True
        assert result == {"err_msg": "请求重复,请稍后再试"}

    def test_overflow_pop_first_drops_oldest_and_executes(self):
        # pop_first=True：超出并发数时移除最老请求，新请求正常执行
        cache_obj = PendingStateCache("locker-4")
        cache_obj.set_storage_cache(["u-old1", "u-old2"], timeout=60)

        ok, result = get_pending_result(
            lambda: "done",
            lambda r: True,
            loop_count=1,
            sleep_time=0.01,
            unique_key="u-new",
            run_func_count=2,
            pop_first=True,
            locker_key="locker-4",
        )
        assert ok is True
        assert result == {"data": "done"}
        # 最老请求被挤出，新请求执行后自身也被清理
        assert "u-old1" not in (cache_obj.get_storage_cache() or [])

    def test_exception_inside_lock_returns_internal_error(self):
        def broken():
            raise RuntimeError("boom")

        ok, result = get_pending_result(
            broken,
            lambda r: True,
            loop_count=1,
            sleep_time=0.01,
            unique_key="u-err",
            locker_key="locker-err",
        )
        assert ok is False
        assert result == {"err_msg": "内部错误"}

    def test_locker_key_required(self):
        import pytest

        with pytest.raises(KeyError):
            get_pending_result(
                lambda: 1, lambda r: True, loop_count=1, sleep_time=0.01
            )
