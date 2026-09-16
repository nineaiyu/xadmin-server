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
