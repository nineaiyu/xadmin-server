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
# 任务耗时 histogram 的跨进程聚合（redis hash："task|le:<bound>" / "task|count" / "task|sum"）
TASK_DURATION_REDIS_KEY = "xadmin:metrics:celery_task_durations"
# 耗时桶（与进程内 Histogram 一致；渲染按序输出，末尾补 "+Inf"=count）
_TASK_DURATION_BUCKETS = (0.1, 0.5, 1, 5, 30, 60, 300, 900, 3600)


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
    if duration is not None:
        _record_task_duration_redis(name, duration)


def _record_task_duration_redis(name: str, duration: float) -> None:
    """任务耗时写入 redis 累积桶（Prometheus histogram 语义：le >= duration 的桶 +1）。"""
    try:
        from django_redis import get_redis_connection

        conn = get_redis_connection("default")
        for bound in _TASK_DURATION_BUCKETS:
            if duration <= bound:
                conn.hincrby(TASK_DURATION_REDIS_KEY, f"{name}|le:{bound}", 1)
        conn.hincrby(TASK_DURATION_REDIS_KEY, f"{name}|count", 1)
        conn.hincrbyfloat(TASK_DURATION_REDIS_KEY, f"{name}|sum", duration)
    except Exception as e:  # noqa: BLE001 指标旁路：redis 不可用不影响任务执行
        logger.debug(f"record task duration to redis failed: {e}")


def _escape_label(value: str) -> str:
    """Prometheus 文本 label 值转义（反斜杠 / 双引号 / 换行）。"""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_task_redis() -> bytes:
    """渲染 redis 聚合的 celery 任务指标（计数 + 耗时 histogram；无数据/redis 不可用返回空）。"""
    try:
        from django_redis import get_redis_connection

        conn = get_redis_connection("default")
        count_raw = conn.hgetall(TASK_REDIS_KEY)
        duration_raw = conn.hgetall(TASK_DURATION_REDIS_KEY)
    except Exception as e:  # noqa: BLE001 指标旁路：不影响端点其余指标
        logger.debug(f"render task metrics from redis failed: {e}")
        return b""
    lines = _render_task_counts(count_raw) + _render_task_durations(duration_raw)
    return ("\n".join(lines) + "\n").encode() if lines else b""


def _decode_text(value) -> str:
    """原始连接（get_redis_connection）不做 decode，key/value 可能为 bytes。"""
    return value.decode() if isinstance(value, bytes) else str(value)


def _render_task_counts(raw) -> list:
    if not raw:
        return []
    lines = [
        "# HELP xadmin_celery_tasks_total Celery 任务执行数（按任务名与终态，跨进程聚合）",
        "# TYPE xadmin_celery_tasks_total counter",
    ]
    for key, value in sorted(raw.items()):
        name, _, status = _decode_text(key).partition("|")
        if not name:
            continue
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        lines.append(
            f'xadmin_celery_tasks_total{{task="{_escape_label(name)}",status="{_escape_label(status)}"}} {count}'
        )
    return lines


def _render_task_durations(raw) -> list:
    """渲染耗时 histogram（累积桶 + sum/count；供任务 P95 计算）。"""
    if not raw:
        return []
    grouped: dict = {}
    for key, value in raw.items():
        name, _, field = _decode_text(key).partition("|")
        if not name:
            continue
        item = grouped.setdefault(name, {"buckets": {}, "count": 0, "sum": 0.0})
        try:
            if field.startswith("le:"):
                item["buckets"][field[3:]] = int(value)
            elif field == "count":
                item["count"] = int(value)
            elif field == "sum":
                item["sum"] = float(value)
        except (TypeError, ValueError):
            continue
    if not grouped:
        return []
    lines = [
        "# HELP xadmin_celery_task_duration_seconds Celery 任务耗时（秒，跨进程聚合）",
        "# TYPE xadmin_celery_task_duration_seconds histogram",
    ]
    for name in sorted(grouped):
        item = grouped[name]
        label = _escape_label(name)
        for bound in _TASK_DURATION_BUCKETS:
            # 零值桶也输出（Prometheus histogram 惯例：系列完整，histogram_quantile 直接可用）
            bucket_value = item["buckets"].get(f"{bound}", 0)
            lines.append(f'xadmin_celery_task_duration_seconds_bucket{{task="{label}",le="{bound}"}} {bucket_value}')
        lines.append(f'xadmin_celery_task_duration_seconds_bucket{{task="{label}",le="+Inf"}} {item["count"]}')
        lines.append(f'xadmin_celery_task_duration_seconds_sum{{task="{label}"}} {item["sum"]}')
        lines.append(f'xadmin_celery_task_duration_seconds_count{{task="{label}"}} {item["count"]}')
    return lines


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
