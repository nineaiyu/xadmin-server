#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""服务健康探测（DB / Redis / Celery）。

healthz（common/api/common.py）与系统监控面板（system/views/monitor.py）
共用同一套探测逻辑，保证两处口径一致（状态 + 耗时）。探测函数返回
(ok, cost)：ok 为布尔，cost 为耗时秒数或异常信息字符串。
"""

import time

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
