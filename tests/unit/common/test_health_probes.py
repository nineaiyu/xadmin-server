#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""健康探测：并行化与总超时预算（故障依赖不得拖挂容器 healthcheck）。

背景（故障演练实测）：Redis 被冻结时串行探测累计超过 8 秒，超过 compose
healthcheck 的 5 秒超时，健康状态会被误判——probe_all 以并行 + 预算收敛。
"""

import time


class TestProbeAll:
    def test_returns_all_three_probes(self):
        from common.utils import health

        results = health.probe_all()
        assert set(results.keys()) == {"db", "redis", "celery"}
        for value in results.values():
            assert isinstance(value, tuple) and len(value) == 2

    def test_slow_probe_times_out_without_blocking(self, monkeypatch):
        """单项超预算：该项判失败，且总耗时不被慢探测拖长。"""
        from common.utils import health

        def slow():
            time.sleep(1.2)
            return True, 1.2

        monkeypatch.setattr(health, "probe_redis", slow)
        monkeypatch.setattr(health, "probe_db", lambda: (True, 0.001))
        monkeypatch.setattr(health, "probe_celery", lambda: (True, 0.001))

        started = time.time()
        results = health.probe_all(timeout=0.2)
        elapsed = time.time() - started

        assert results["redis"] == (False, "probe timeout")
        assert results["db"] == (True, 0.001)
        assert results["celery"] == (True, 0.001)
        assert elapsed < 0.8, f"总耗时应受预算约束，实际 {elapsed:.2f}s"

    def test_probe_exception_returns_failure(self, monkeypatch):
        """探测函数抛异常：按失败返回，不中断整体探测。"""
        from common.utils import health

        def boom():
            raise RuntimeError("redis down")

        monkeypatch.setattr(health, "probe_redis", boom)
        results = health.probe_all(timeout=1)
        ok, cost = results["redis"]
        assert ok is False
        assert "redis down" in cost


class TestProbeCeleryCache:
    """celery 探测缓存（2030-04）：TTL 内复用、过期返回旧值 + 后台刷新、跳过配置。"""

    def test_cache_hit_returns_last_value(self, monkeypatch):
        import time

        import common.utils.health as health

        monkeypatch.setattr(health, "_celery_probe_cache", {"at": time.time(), "value": (True, 0.5)})
        assert health.probe_celery() == (True, 0.5)

    def test_expired_cache_returns_stale_and_refreshes_in_background(self, monkeypatch):
        """过期时立即返回旧值（不阻塞），后台线程刷新出真实结果。"""
        import sys
        import time
        import types

        import common.utils.health as health

        cache = {"at": 0.0, "value": (False, 0.0)}
        monkeypatch.setattr(health, "_celery_probe_cache", cache)
        monkeypatch.setattr(health, "_CELERY_PROBE_TIMEOUT", 0.05)

        class _FakeInspect:
            @staticmethod
            def ping():
                time.sleep(0.1)  # 模拟真实收集窗口耗时：确保本次调用先返回旧值
                return {"celery@w1": {"ok": "pong"}}

        class _FakeControl:
            @staticmethod
            def inspect(timeout=None):
                return _FakeInspect()

        monkeypatch.setitem(
            sys.modules,
            "server.celery",
            types.SimpleNamespace(app=types.SimpleNamespace(control=_FakeControl())),
        )

        # 前序测试可能触发过真实后台刷新：等其释放锁（避免本次刷新被防重入锁跳过），
        # 且该线程可能横跨 monkeypatch 时刻写入了本 dict——等锁后再重置一次缓存状态
        for _ in range(300):
            if health._celery_probe_refreshing.acquire(blocking=False):
                health._celery_probe_refreshing.release()
                break
            time.sleep(0.02)
        cache["at"] = 0.0
        cache["value"] = (False, 0.0)

        assert health.probe_celery() == (False, 0.0)  # 首次返回旧值（毫秒级）
        for _ in range(100):  # 等后台刷新写入
            if cache["value"][0] is True:
                break
            time.sleep(0.01)
        assert cache["value"][0] is True
        assert cache["at"] > 0

    def test_skip_setting_short_circuits(self, settings):
        import common.utils.health as health

        settings.HEALTH_CHECK_SKIP_CELERY = True
        assert health.probe_celery() == (False, 0.0)
