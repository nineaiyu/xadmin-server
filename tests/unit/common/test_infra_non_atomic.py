#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""基础设施端点的请求事务豁免（2026-09-18 真丢包演练修复）。

背景：``ATOMIC_REQUESTS=True`` 下每个请求进入视图前开启事务（``ensure_connection``
建立数据库连接），DB 故障（丢包/池超时）时 health 等基础设施端点会在请求入口直接
500——监控视角上"服务完全不可用"。health / metrics / csp-report 不依赖数据库，
用 Django 官方 ``transaction.non_atomic_requests`` 包装 URLconf callback 豁免事务。

守护口径与 Django ``make_view_atomic`` 一致：检查 ``resolve`` 出的 callback 上
``_non_atomic_requests`` 是否包含 default 别名。
"""

from django.urls import resolve


def _non_atomic_aliases(url):
    match = resolve(url)
    return set(getattr(match.func, "_non_atomic_requests", set()))


class TestInfraEndpointsNonAtomic:
    def test_health_is_non_atomic(self):
        assert "default" in _non_atomic_aliases("/api/common/api/health")

    def test_metrics_is_non_atomic(self):
        assert "default" in _non_atomic_aliases("/api/common/api/metrics")

    def test_csp_report_is_non_atomic(self):
        assert "default" in _non_atomic_aliases("/api/common/api/csp-report")

    def test_business_endpoint_still_atomic(self):
        """对照：业务端点不应被误豁免（仍由 ATOMIC_REQUESTS 保护）。"""
        assert "default" not in _non_atomic_aliases("/api/system/login/basic")
