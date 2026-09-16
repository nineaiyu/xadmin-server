#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""服务健康探测（DB / Redis / Celery）。

healthz（common/api/common.py）与系统监控面板（system/views/monitor.py）
共用同一套探测逻辑，保证两处口径一致（状态 + 耗时）。探测函数返回
(ok, cost)：ok 为布尔，cost 为耗时秒数或异常信息字符串。
"""

import time
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.cache import cache


def probe_db():
    """SELECT 1 探测数据库连通性，不依赖任何业务表。"""
    t1 = time.time()
    try:
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True, time.time() - t1
    except Exception as e:  # noqa: BLE001 探测失败需返回原因而非中断
        return False, str(e)


def probe_redis():
    """缓存 Redis set/get 往返探测。"""
    t1 = time.time()
    try:
        cache.set("HEALTH_CHECK", "1", 10)
        got = cache.get("HEALTH_CHECK")
        if got == "1":
            return True, time.time() - t1
        return False, "Value not match"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def probe_celery(timeout=1):
    """探测在线 worker（inspect ping）。E2E/单进程模式下可显式跳过。"""
    if getattr(settings, "HEALTH_CHECK_SKIP_CELERY", False):
        return False, 0.0
    t1 = time.time()
    try:
        from server.celery import app

        workers = app.control.inspect(timeout=timeout).ping()
        return bool(workers), time.time() - t1
    except Exception as e:  # noqa: BLE001
        return False, str(e)


# 健康检查总预算（秒）：**共享一个 deadline**（逐项各自等待会累积成 3×timeout）。
# 实测收敛（2029-10 复测）：单次预算 1s——冻结时两项超时探测合计 ≤1s，加上请求
# 路径（中间件/配置读）的快速失败，整体响应稳定低于容器 healthcheck 的 5s 超时。
PROBE_BUDGET_SECONDS = 1
# 池容量大于探测项数：故障依赖可能让个别探测 future 长时间不收敛
# （celery inspect 对不可达 broker 的内部重试不受 timeout 参数完全约束），
# 池被占满前不影响其余探测的调度；占满后退化为立即超时（仍为快速失败）。
_probe_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="health-probe")


def probe_all(timeout=PROBE_BUDGET_SECONDS):
    """并行执行 db/redis/celery 三项探测，返回 {name: (ok, cost)}。

    单项超预算即返回 ``(False, "probe timeout")``——探测线程由各自的连接超时
    自行收敛，不阻断响应。背景（2026-09-16 故障演练实测）：Redis 被冻结时
    串行探测累计超过 8 秒（且占用请求处理线程），超过容器 healthcheck 的
    5 秒超时，健康状态被误判为不健康。
    """
    probes = {"db": probe_db, "redis": probe_redis, "celery": probe_celery}
    futures = {name: _probe_pool.submit(fn) for name, fn in probes.items()}
    deadline = time.monotonic() + timeout
    results = {}
    for name, future in futures.items():
        remaining = deadline - time.monotonic()
        # 已完成的探测直接取结果（预算只约束「等待」，不误伤已完成项）
        if not future.done() and remaining <= 0:
            results[name] = (False, "probe timeout")
            continue
        try:
            results[name] = future.result(timeout=max(remaining, 0))
        except TimeoutError:
            results[name] = (False, "probe timeout")
        except Exception as e:  # noqa: BLE001 探测异常按失败返回
            results[name] = (False, str(e))
    return results
