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


def render_metrics():
    """返回 (payload, content_type)。"""
    return generate_latest(), CONTENT_TYPE_LATEST
