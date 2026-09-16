#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""Prometheus 指标采集（可选启用）。

设计取舍：
- 默认关闭（``METRICS_ENABLED=false``）：未启用时中间件不挂载、端点返回 404，
  避免指标端点意外对外暴露；
- 依赖 ``prometheus-client`` 缺失时降级为 no-op，不影响服务启动与请求链路；
- 采集失败静默跳过：指标是旁路能力，任何异常都不得影响主流程。
"""

from common.utils import get_logger

logger = get_logger(__name__)

try:  # 依赖缺失时保持可用（降级为 no-op）
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

    _DEP_AVAILABLE = True
except Exception:  # noqa: BLE001 - 缺失依赖不应阻断启动
    _DEP_AVAILABLE = False

_REQUESTS = None
_DURATION = None
_TASKS = None
_TASK_DURATION = None

if _DEP_AVAILABLE:
    _REQUESTS = Counter(
        "xadmin_http_requests_total",
        "HTTP 请求总数",
        ["method", "view", "status"],
    )
    _DURATION = Histogram(
        "xadmin_http_request_duration_seconds",
        "HTTP 请求耗时（秒）",
        ["method", "view"],
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
    )
    # 任务指标不注册进默认 registry：任务在 worker 进程执行，进程内计数器 web 的
    # /metrics 端点读不到（本地仅作单测/进程内观察用）。对外统一用 redis 跨进程
    # 聚合渲染（TASK_REDIS_KEY，见 record_task_result / _render_task_redis）。
    _TASKS = Counter(
        "xadmin_celery_tasks_total",
        "Celery 任务执行数（按任务名与终态）",
        ["task", "status"],
        registry=None,
    )
    _TASK_DURATION = Histogram(
        "xadmin_celery_task_duration_seconds",
        "Celery 任务耗时（秒）",
        ["task"],
        buckets=(0.1, 0.5, 1, 5, 30, 60, 300, 900, 3600),
        registry=None,
    )

# 任务指标跨进程聚合（redis hash："task|status" -> count，无 TTL）。
# worker 侧写入（record_task_result），web 端点渲染时附加（_render_task_redis）——
# SLO「任务成功率」的数据源（口径见 docs/ops/observability.md）。
TASK_REDIS_KEY = "xadmin:metrics:celery_tasks"


def metrics_available() -> bool:
    """prometheus-client 是否可用（未安装时端点应返回 503 而非 500）。"""
    return _DEP_AVAILABLE


def record_http_request(method: str, view: str, status: int, duration: float) -> None:
    """记录一次 HTTP 请求；任何异常都不得影响主流程。"""
    if not _DEP_AVAILABLE:
        return
    try:
        view_name = view or "unknown"
        _REQUESTS.labels(method=method, view=view_name, status=str(status)).inc()
        _DURATION.labels(method=method, view=view_name).observe(duration)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"record http metrics failed: {e}")


def record_task_result(task_name: str, status: str, duration: float | None = None) -> None:
    """记录一次 celery 任务终态与耗时（SLO：任务成功率 = SUCCESS / total）。

    信号接线见 common/celery/metrics.py；任何异常都不得影响任务执行。
    跨进程口径：进程内计数仅供本进程观察，另写 redis 聚合（TASK_REDIS_KEY）——
    worker 进程执行的任务由此可被 web 的 /metrics 端点拉取（SLO 数据源）。
    """
    if not _DEP_AVAILABLE:
        return
    name = task_name or "unknown"
    status_name = str(status or "UNKNOWN")
    try:
        _TASKS.labels(task=name, status=status_name).inc()
        if duration is not None:
            _TASK_DURATION.labels(task=name).observe(duration)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"record task metrics failed: {e}")
    try:
        from django_redis import get_redis_connection

        get_redis_connection("default").hincrby(TASK_REDIS_KEY, f"{name}|{status_name}", 1)
    except Exception as e:  # noqa: BLE001 指标旁路：redis 不可用不影响任务执行
        logger.debug(f"record task metrics to redis failed: {e}")


def _escape_label(value: str) -> str:
    """Prometheus 文本 label 值转义（反斜杠 / 双引号 / 换行）。"""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_task_redis() -> bytes:
    """渲染 redis 聚合的 celery 任务计数（Prometheus 文本；无数据/redis 不可用时返回空）。"""
    try:
        from django_redis import get_redis_connection

        raw = get_redis_connection("default").hgetall(TASK_REDIS_KEY)
    except Exception as e:  # noqa: BLE001 指标旁路：不影响端点其余指标
        logger.debug(f"render task metrics from redis failed: {e}")
        return b""
    if not raw:
        return b""
    lines = [
        "# HELP xadmin_celery_tasks_total Celery 任务执行数（按任务名与终态，跨进程聚合）",
        "# TYPE xadmin_celery_tasks_total counter",
    ]
    for key, value in sorted(raw.items()):
        # 原始连接（get_redis_connection）不做 decode，key/value 为 bytes
        key_text = key.decode() if isinstance(key, bytes) else str(key)
        name, _, status = key_text.partition("|")
        if not name:
            continue
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        lines.append(
            f'xadmin_celery_tasks_total{{task="{_escape_label(name)}",status="{_escape_label(status)}"}} {count}'
        )
    return ("\n".join(lines) + "\n").encode()


def render_metrics():
    """返回 (payload, content_type)。

    celery 任务指标来自 redis 跨进程聚合（worker 写入，本端点附加渲染）——
    进程内 Counter/Histogram 未注册进 registry（见 _TASKS/_TASK_DURATION），
    避免与聚合口径在输出中重复。
    """
    payload = generate_latest()
    task_payload = _render_task_redis()
    if task_payload:
        payload += task_payload
    return payload, CONTENT_TYPE_LATEST
