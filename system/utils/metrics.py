#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""监控面板指标采集（MonitorViewSet 与 WS 实时推送共用，保证口径一致）。

全部为同步函数：HTTP action 直接调用；WS 消费者以 database_sync_to_async
（线程池）包裹执行，避免 psutil/inspect 的阻塞调用卡住事件循环。
"""

import concurrent.futures
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from common.core.config import SysConfig
from common.utils import get_logger
from common.utils.connection import get_redis_client
from common.utils.health import probe_celery, probe_db, probe_redis

logger = get_logger(__name__)

SLOW_REQUEST_WINDOW_HOURS = 24
SLOW_REQUEST_LIMIT = 20
MONITOR_TREND_POINTS = 60


def collect_live_metrics():
    """主机资源实时快照（psutil 直读）；采集失败返回 None，调用方回退心跳值。"""
    try:
        import psutil

        from common.utils.common import get_boot_time, get_cpu_load, get_cpu_percent, get_disk_usage, get_memory_usage

        net = psutil.net_io_counters()
        return {
            "cpu_percent": get_cpu_percent(),
            "cpu_load": get_cpu_load(),
            "memory_used": get_memory_usage(),
            "disk_used": get_disk_usage(path=settings.PROJECT_DIR),
            "swap_percent": psutil.swap_memory().percent,
            "cpu_count": psutil.cpu_count(),
            "process_count": len(psutil.pids()),
            "net_sent_mb": round(net.bytes_sent / 1024 / 1024, 1),
            "net_recv_mb": round(net.bytes_recv / 1024 / 1024, 1),
            "boot_time": get_boot_time(),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("collect live metrics failed: %s", e)
        return None


def collect_latest_and_trend():
    """心跳表最新值 + 最近趋势（common.Monitor 30s 落盘）。"""
    from common.models import Monitor

    latest = Monitor.objects.order_by("-created_time").first()
    trend = list(
        Monitor.objects.order_by("-created_time").values(
            "cpu_percent", "cpu_load", "memory_used", "disk_used", "created_time"
        )[:MONITOR_TREND_POINTS]
    )[::-1]
    return (
        {
            "cpu_percent": latest.cpu_percent,
            "cpu_load": latest.cpu_load,
            "memory_used": latest.memory_used,
            "disk_used": latest.disk_used,
            "boot_time": latest.boot_time,
            "created_time": latest.created_time,
        }
        if latest
        else None
    ), trend


def collect_services():
    """核心服务健康：DB / Redis / Celery 状态与探测耗时。"""
    db_ok, db_cost = probe_db()
    redis_ok, redis_cost = probe_redis()
    celery_ok, celery_cost = probe_celery()
    return {
        "db": {"status": db_ok, "cost": db_cost},
        "redis": {"status": redis_ok, "cost": redis_cost},
        "celery": {"status": celery_ok, "cost": celery_cost},
        "status": all([db_ok, redis_ok, celery_ok]),
    }


def collect_redis_info():
    """缓存 Redis INFO 关键指标与 Celery broker 队列长度。"""
    data = {}
    try:
        client = get_redis_client()
        info = client.info()
        keyspace_hits = info.get("keyspace_hits", 0)
        keyspace_misses = info.get("keyspace_misses", 0)
        hits_total = keyspace_hits + keyspace_misses
        data["redis"] = {
            "version": info.get("redis_version"),
            "used_memory_human": info.get("used_memory_human"),
            "maxmemory_human": info.get("maxmemory_human", "0B"),
            "connected_clients": info.get("connected_clients"),
            "uptime_days": info.get("uptime_in_days"),
            "keyspace_hits": keyspace_hits,
            "keyspace_misses": keyspace_misses,
            "hit_rate": round(keyspace_hits / hits_total * 100, 2) if hits_total else None,
            "dbs": {
                db: entry.get("keys", 0) for db, entry in client.info("keyspace").items() if isinstance(entry, dict)
            },
        }
    except Exception as e:  # noqa: BLE001 Redis 不可用时面板不白屏
        data["redis"] = {"status": False, "error": str(e)}
    try:
        import redis as redis_lib

        broker = redis_lib.from_url(settings.CELERY_BROKER_URL)
        data["queues"] = {queue: broker.llen(queue) for queue in ("celery", "default", "heavy")}
        broker.close()
    except Exception as e:  # noqa: BLE001 memory broker / broker 不可用时不影响其余指标
        data["queues"] = {"error": str(e)}
    return data


def collect_celery_status():
    """Celery worker 汇总（stats/active/reserved 并发广播）。"""
    from server.celery import app

    if getattr(settings, "HEALTH_CHECK_SKIP_CELERY", False):
        return {"workers": [], "total": 0, "skipped": True}
    inspect = app.control.inspect(timeout=1)
    # 各自是一次独立广播（每个最长阻塞 timeout），并发提交把墙钟时间压回 ~1s
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        stats_f = executor.submit(inspect.stats)
        active_f = executor.submit(inspect.active)
        reserved_f = executor.submit(inspect.reserved)
        stats = stats_f.result() or {}
        active = active_f.result() or {}
        reserved = reserved_f.result() or {}
    workers = []
    for name, stat in stats.items():
        pool_info = stat.get("pool") or {}
        workers.append(
            {
                "name": name,
                "concurrency": pool_info.get("max-concurrency"),
                "active": len(active.get(name, [])),
                "reserved": len(reserved.get(name, [])),
                "uptime": stat.get("clock"),
            }
        )
    return {"workers": workers, "total": len(workers)}


def collect_slow_requests():
    """慢请求 Top N（最近窗口内 exec_time 超阈值的操作日志）。"""
    from system.models.log import OperationLog

    threshold = SysConfig.SLOW_REQUEST_THRESHOLD
    deadline = timezone.now() - timedelta(hours=SLOW_REQUEST_WINDOW_HOURS)
    rows = (
        OperationLog.objects.filter(created_time__gte=deadline, exec_time__gte=threshold)
        .order_by("-exec_time")
        .values("pk", "module", "path", "method", "exec_time", "status_code", "creator__username", "created_time")[
            :SLOW_REQUEST_LIMIT
        ]
    )
    return {"threshold": threshold, "results": list(rows)}
