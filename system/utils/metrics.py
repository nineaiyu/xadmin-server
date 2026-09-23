#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""监控面板指标采集（MonitorViewSet 与 WS 实时推送共用，保证口径一致）。

全部为同步函数：HTTP action 直接调用；WS 消费者以 database_sync_to_async
（线程池）包裹执行，避免 psutil/inspect 的阻塞调用卡住事件循环。
"""

import concurrent.futures
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from common.core.config import SysConfig
from common.utils import get_logger, get_net_io_bytes
from common.utils.connection import get_redis_client
from common.utils.health import probe_celery, probe_db, probe_redis

logger = get_logger(__name__)

SLOW_REQUEST_WINDOW_HOURS = 24
SLOW_REQUEST_LIMIT = 20
MONITOR_TREND_POINTS = 60

# 网卡速率：psutil 只提供累计计数器，速率由进程内上次快照差分得出
# （WS 5s 推送与 HTTP 轮询共享，窗口为两次采集的实际间隔）
_net_rate_state = {"time": None, "sent": 0, "recv": 0}
_net_rate_lock = threading.Lock()


def _net_rates(sent_bytes, recv_bytes):
    """返回 (上行 KB/s, 下行 KB/s)；首次采集或计数器重置（重启）时为 (None, None)。"""
    now = time.monotonic()
    with _net_rate_lock:
        prev = dict(_net_rate_state)
        _net_rate_state.update(time=now, sent=sent_bytes, recv=recv_bytes)
    if prev["time"] is None:
        return None, None
    elapsed = now - prev["time"]
    sent_delta = sent_bytes - prev["sent"]
    recv_delta = recv_bytes - prev["recv"]
    if elapsed <= 0 or sent_delta < 0 or recv_delta < 0:
        return None, None
    return round(sent_delta / 1024 / elapsed, 2), round(recv_delta / 1024 / elapsed, 2)


def collect_live_metrics():
    """主机资源实时快照（psutil 直读）；采集失败返回 None，调用方回退心跳值。"""
    try:
        import psutil

        from common.utils.common import get_boot_time, get_cpu_load, get_cpu_percent, get_disk_usage, get_memory_usage

        sent_bytes, recv_bytes = get_net_io_bytes()
        net_sent_rate, net_recv_rate = _net_rates(sent_bytes, recv_bytes)
        return {
            "cpu_percent": get_cpu_percent(),
            "cpu_load": get_cpu_load(),
            "memory_used": get_memory_usage(),
            "disk_used": get_disk_usage(path=settings.PROJECT_DIR),
            "swap_percent": psutil.swap_memory().percent,
            "cpu_count": psutil.cpu_count(),
            "process_count": len(psutil.pids()),
            "net_sent_mb": round(sent_bytes / 1024 / 1024, 1),
            "net_recv_mb": round(recv_bytes / 1024 / 1024, 1),
            "net_sent_rate": net_sent_rate,
            "net_recv_rate": net_recv_rate,
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


# 健康总览评分：资源项按超阈值程度分档（≤阈值 healthy / 超阈值 warning /
# 超阈值 20% critical）；服务项 DB/Redis 失败为 critical，Celery 未探测到
# worker 降为 warning（后台任务离线不阻断核心服务，避免常亮红旗）。
HEALTH_CRITICAL_RATIO = 1.2
HEALTH_RESOURCE_PENALTY = {"warning": 8, "critical": 20}
HEALTH_SERVICE_PENALTY = {"warning": 10, "critical": 25}
HEALTH_LABELS = {
    "cpu_percent": ("CPU usage", "%"),
    "cpu_load": ("CPU load", ""),
    "memory_used": ("Memory usage", "%"),
    "disk_used": ("Disk usage", "%"),
}


def _resource_entry(key, value, threshold):
    from django.utils.translation import gettext as _

    label, unit = HEALTH_LABELS[key]
    try:
        value = round(float(value), 2)
    except (TypeError, ValueError):
        value = None
    if value is None or not threshold:
        status = "unknown"
    elif value >= float(threshold) * HEALTH_CRITICAL_RATIO:
        status = "critical"
    elif value >= float(threshold):
        status = "warning"
    else:
        status = "healthy"
    return {
        "key": key,
        "group": "resource",
        "label": str(_(label)),
        "value": value,
        "unit": unit,
        "threshold": threshold,
        "status": status,
    }


def _service_entry(key, label, probe, degraded=False, skipped=False):
    from django.utils.translation import gettext as _

    probe = probe or {}
    ok = bool(probe.get("status"))
    if skipped:
        status = "unknown"
    elif ok:
        status = "healthy"
    elif degraded:
        status = "warning"
    else:
        status = "critical"
    return {
        "key": key,
        "group": "service",
        "label": str(_(label)),
        "status": status,
        "cost": probe.get("cost"),
    }


def collect_health_summary(live=None, services=None, celery_skipped=None):
    """系统健康总览：资源阈值分档 + 服务探测 + 告警计数 → 总状态与健康分。

    live/services 允许调用方复用已采集结果（overview 与 WS panel 帧各采一次）。
    """

    from system.utils.monitor_events import alert_counts

    if live is None:
        live = collect_live_metrics()
    if services is None:
        services = collect_services()
    if celery_skipped is None:
        celery_skipped = bool(getattr(settings, "HEALTH_CHECK_SKIP_CELERY", False))
    live = live or {}

    items = [
        _resource_entry("cpu_percent", live.get("cpu_percent"), settings.SECURITY_MONITOR_CPU_PERCENT_MAX),
        _resource_entry("cpu_load", live.get("cpu_load"), settings.SECURITY_MONITOR_CPU_LOAD_MAX),
        _resource_entry("memory_used", live.get("memory_used"), settings.SECURITY_MONITOR_MEMORY_USED_MAX),
        _resource_entry("disk_used", live.get("disk_used"), settings.SECURITY_MONITOR_DISK_USED_MAX),
        _service_entry("db", "Database", services.get("db")),
        _service_entry("redis", "Redis cache", services.get("redis")),
        _service_entry("celery", "Celery", services.get("celery"), degraded=True, skipped=celery_skipped),
    ]

    penalty = 0
    for entry in items:
        if entry["status"] == "unknown":
            continue
        if entry["group"] == "service":
            penalty += HEALTH_SERVICE_PENALTY.get(entry["status"], 0)
        else:
            penalty += HEALTH_RESOURCE_PENALTY.get(entry["status"], 0)
    statuses = {entry["status"] for entry in items}
    if "critical" in statuses:
        overall = "critical"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "healthy"
    return {
        "status": overall,
        "score": max(0, 100 - penalty),
        "items": items,
        "alerts": alert_counts(),
        "checked_at": timezone.localtime(timezone.now()).isoformat(),
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


TASK_HEALTH_WINDOW_DAYS = 1
TASK_HEALTH_RECENT_FAILURES = 5
TASK_HEALTH_TOP_TASKS = 10
# 健康色阈值（终态成功率）：≥99% healthy / ≥90% degraded / 其余 failing；
# 样本不足 MIN_SAMPLE 时视为 healthy（样本太少不误报）
TASK_HEALTH_MIN_SAMPLE = 10


def collect_task_health(days: int = None):
    """任务执行健康度（近 N 天聚合）。

    - 成功率按终态（SUCCESS/FAILURE/REVOKED）计算，PENDING/RUNNING 在途不计；
    - state：healthy / degraded / failing 三档健康色，样本不足时恒 healthy；
    - per_task 按执行次数取 Top N，供监控页定位高频异常任务。
    """
    from django.db.models import Count, Q

    from system.models.task import TaskExecution

    if days is None:
        days = TASK_HEALTH_WINDOW_DAYS
    deadline = timezone.now() - timedelta(days=days)
    terminal = Q(status__in=[TaskExecution.Status.SUCCESS, TaskExecution.Status.FAILURE, TaskExecution.Status.REVOKED])
    base = TaskExecution.objects.filter(created_time__gte=deadline)

    by_status = dict(base.values_list("status").annotate(n=Count("pk")))
    total = sum(by_status.values())
    success = by_status.get(TaskExecution.Status.SUCCESS, 0)
    failure = by_status.get(TaskExecution.Status.FAILURE, 0)
    revoked = by_status.get(TaskExecution.Status.REVOKED, 0)
    terminal_count = success + failure + revoked
    success_rate = round(success / terminal_count, 4) if terminal_count else None

    if terminal_count < TASK_HEALTH_MIN_SAMPLE:
        state = "healthy"
    elif success_rate >= 0.99:
        state = "healthy"
    elif success_rate >= 0.90:
        state = "degraded"
    else:
        state = "failing"

    # 平均耗时 Python 侧求值（sqlite 对时间差聚合支持不一，approval_stats 同策略；
    # 有界窗口 + 截断 1000 条防大表）
    durations = base.filter(terminal, date_start__isnull=False, date_finished__isnull=False).values_list(
        "date_start", "date_finished"
    )[:1000]
    costs = [(fin - start).total_seconds() for start, fin in durations if fin and start]
    avg_cost = round(sum(costs) / len(costs), 3) if costs else None
    recent_failures = list(
        base.filter(status__in=[TaskExecution.Status.FAILURE, TaskExecution.Status.REVOKED])
        .order_by("-date_finished")
        .values("pk", "name", "status", "date_finished")[:TASK_HEALTH_RECENT_FAILURES]
    )
    per_task = list(
        base.values("name")
        .annotate(
            total=Count("pk"),
            success=Count("pk", filter=Q(status=TaskExecution.Status.SUCCESS)),
        )
        .order_by("-total")[:TASK_HEALTH_TOP_TASKS]
    )
    for item in per_task:
        item["success_rate"] = round(item.pop("success") / item["total"], 4) if item["total"] else None

    return {
        "window_days": days,
        "total": total,
        "success": success,
        "failure": failure,
        "revoked": revoked,
        "running": by_status.get(TaskExecution.Status.RUNNING, 0),
        "pending": by_status.get(TaskExecution.Status.PENDING, 0),
        "success_rate": success_rate,
        "state": state,
        "avg_cost_seconds": avg_cost,
        "recent_failures": recent_failures,
        "per_task": per_task,
    }
