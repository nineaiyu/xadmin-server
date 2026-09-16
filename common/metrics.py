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
    _TASKS = Counter(
        "xadmin_celery_tasks_total",
        "Celery 任务执行数（按任务名与终态）",
        ["task", "status"],
    )
    _TASK_DURATION = Histogram(
        "xadmin_celery_task_duration_seconds",
        "Celery 任务耗时（秒）",
        ["task"],
        buckets=(0.1, 0.5, 1, 5, 30, 60, 300, 900, 3600),
    )


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
    """
    if not _DEP_AVAILABLE:
        return
    try:
        name = task_name or "unknown"
        _TASKS.labels(task=name, status=str(status or "UNKNOWN")).inc()
        if duration is not None:
            _TASK_DURATION.labels(task=name).observe(duration)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"record task metrics failed: {e}")


def render_metrics():
    """返回 (payload, content_type)。"""
    return generate_latest(), CONTENT_TYPE_LATEST
