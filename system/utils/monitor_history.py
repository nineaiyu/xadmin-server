#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""监控历史趋势：时间范围筛选、时间桶聚合、多指标对比与报表数据组装。

数据源为 common.Monitor 心跳表（30s 一条）。网络指标存的是累计收发量，
速率由相邻采样差分得出（进程/系统重启后计数器归零的负差值按缺失处理，
不伪造 0 值峰值）。聚合在 Python 侧按时间桶完成：心跳表在保留期（默认
30 天）内最多十万量级行，一次取回后分桶比多次 DB 聚合更可读且便于单测。
"""

import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)

# 预设时间范围（键 → 秒），与前端筛选项一一对应
HISTORY_RANGES = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800, "30d": 2592000}
MAX_RANGE_SECONDS = 30 * 86400
# 聚合粒度（键 → 秒）；auto 按窗口长度推导（目标 120~360 个点）
HISTORY_INTERVALS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}
AUTO_STEPS = ((7200, "1m"), (43200, "5m"), (172800, "15m"), (1209600, "1h"))
AUTO_FALLBACK = "1d"

DIRECT_METRICS = ("cpu_percent", "cpu_load", "memory_used", "disk_used")
RATE_SOURCES = {"net_sent_rate": "net_sent_mb", "net_recv_rate": "net_recv_mb"}
SUPPORTED_METRICS = DIRECT_METRICS + tuple(RATE_SOURCES)
DEFAULT_METRICS = ("cpu_percent", "memory_used", "disk_used")
# 单次取数上限：保留期 30 天 × 30s ≈ 8.6 万行，200k 为未来缩短采集间隔的兜底
ROW_LIMIT = 200000

# 指标展示元数据（label 为惰性翻译，供导出表头复用）
METRIC_META = {
    "cpu_percent": (_("CPU usage"), "%"),
    "cpu_load": (_("CPU load"), ""),
    "memory_used": (_("Memory usage"), "%"),
    "disk_used": (_("Disk usage"), "%"),
    "net_sent_rate": (_("Network upload rate"), "KB/s"),
    "net_recv_rate": (_("Network download rate"), "KB/s"),
}


def _row_fields():
    return DIRECT_METRICS + tuple(RATE_SOURCES.values()) + ("created_time",)


def parse_window_dt(value):
    """解析前端传入的时间（ISO 字符串或秒/毫秒时间戳）；朴素时间按本地时区补全。"""
    if not value:
        return None
    if isinstance(value, datetime.datetime):
        dt = value
    else:
        dt = parse_datetime(str(value))
        if dt is None:
            try:
                timestamp = float(value)
            except (TypeError, ValueError):
                return None
            if timestamp > 1e11:  # 毫秒时间戳
                timestamp /= 1000
            dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.UTC)
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


def resolve_window(range_key=None, start=None, end=None):
    """解析查询窗口：显式 start/end 优先，否则按预设 range 从 end 倒推。"""
    end_dt = parse_window_dt(end) or timezone.now()
    start_dt = parse_window_dt(start)
    if start_dt is None:
        seconds = HISTORY_RANGES.get(range_key, HISTORY_RANGES["1h"])
        start_dt = end_dt - datetime.timedelta(seconds=seconds)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt
    if (end_dt - start_dt).total_seconds() > MAX_RANGE_SECONDS:
        start_dt = end_dt - datetime.timedelta(seconds=MAX_RANGE_SECONDS)
    return start_dt, end_dt


def resolve_interval(seconds, interval=None):
    """解析聚合粒度：显式合法值优先，否则 auto 推导；返回 (键, 秒)。"""
    if interval in HISTORY_INTERVALS:
        return interval, HISTORY_INTERVALS[interval]
    for max_seconds, key in AUTO_STEPS:
        if seconds <= max_seconds:
            return key, HISTORY_INTERVALS[key]
    return AUTO_FALLBACK, HISTORY_INTERVALS[AUTO_FALLBACK]


def resolve_metrics(metrics):
    """指标白名单过滤（空/非法回退默认三件套），保序去重。"""
    if isinstance(metrics, str):
        metrics = [item.strip() for item in metrics.split(",")]
    picked = []
    for item in metrics or []:
        if item in SUPPORTED_METRICS and item not in picked:
            picked.append(item)
    return picked or list(DEFAULT_METRICS)


def metric_label(metric, with_unit=True):
    """指标展示名（含单位），用于图表图例、导出表头。"""
    name, unit = METRIC_META.get(metric, (metric, ""))
    name = str(name)
    return f"{name} ({unit})" if with_unit and unit else name


def _iso(dt):
    return timezone.localtime(dt).isoformat()


def build_raw_points(base_row, rows):
    """原始采样点：直接指标原值 + 网络速率（相邻差分，负值=计数器归零记 None）。"""
    points = []
    prev = base_row
    for row in rows:
        point = {"time": row["created_time"]}
        for field in DIRECT_METRICS:
            point[field] = row[field]
        delta_seconds = (row["created_time"] - prev["created_time"]).total_seconds() if prev else 0
        for rate_key, source in RATE_SOURCES.items():
            point[rate_key] = None
            if prev is not None and delta_seconds > 0:
                delta_mb = row[source] - prev[source]
                if delta_mb >= 0:
                    point[rate_key] = round(delta_mb * 1024 / delta_seconds, 2)
        points.append(point)
        prev = row
    return points


def bucket_points(raw_points, interval_seconds):
    """按时间桶求均值（桶时间取桶起点），跳过缺失值不拉低均值。"""
    if not raw_points:
        return []
    buckets = {}
    for point in raw_points:
        key = int(point["time"].timestamp()) // interval_seconds * interval_seconds
        bucket = buckets.setdefault(key, {"sums": {}, "counts": {}})
        for field, value in point.items():
            if field == "time" or value is None:
                continue
            bucket["sums"][field] = bucket["sums"].get(field, 0.0) + float(value)
            bucket["counts"][field] = bucket["counts"].get(field, 0) + 1
    result = []
    for key in sorted(buckets):
        bucket = buckets[key]
        item = {"time": _iso(datetime.datetime.fromtimestamp(key, tz=datetime.UTC))}
        for field, total in bucket["sums"].items():
            item[field] = round(total / bucket["counts"][field], 2)
        result.append(item)
    return result


def summarize_points(raw_points, metrics):
    """窗口内每指标 min/max/avg/last（last 为窗口内最后一个有效值）。"""
    summary = {}
    for metric in metrics:
        values = [point[metric] for point in raw_points if point.get(metric) is not None]
        if values:
            summary[metric] = {
                "min": round(min(values), 2),
                "max": round(max(values), 2),
                "avg": round(sum(values) / len(values), 2),
                "last": round(values[-1], 2),
            }
        else:
            summary[metric] = {"min": None, "max": None, "avg": None, "last": None}
    return summary


def _compare_item(current, previous):
    if current is None or previous is None:
        return {"prev_avg": None, "delta": None, "percent": None}
    previous = round(previous, 2)
    percent = round((current - previous) / previous * 100, 1) if previous else None
    return {"prev_avg": previous, "delta": round(current - previous, 2), "percent": percent}


def compare_with_previous(model, start_dt, end_dt, metrics, summary):
    """与上一等长窗口对比：直接指标走 DB 均值；速率走首末累计差（少取数）。"""
    result = {}
    span = end_dt - start_dt
    window = {"created_time__gte": start_dt - span, "created_time__lt": start_dt}
    direct = [metric for metric in metrics if metric in DIRECT_METRICS]
    if direct:
        from django.db.models import Avg

        aggregated = model.objects.filter(**window).aggregate(**{metric: Avg(metric) for metric in direct})
        for metric in direct:
            result[metric] = _compare_item(summary.get(metric, {}).get("avg"), aggregated.get(metric))
    rates = [metric for metric in metrics if metric in RATE_SOURCES]
    if rates:
        fields = ("created_time",) + tuple(RATE_SOURCES.values())
        first = model.objects.filter(**window).order_by("created_time").values(*fields).first()
        last = model.objects.filter(**window).order_by("-created_time").values(*fields).first()
        for metric in rates:
            previous = None
            if first and last:
                delta_seconds = (last["created_time"] - first["created_time"]).total_seconds()
                delta_mb = last[RATE_SOURCES[metric]] - first[RATE_SOURCES[metric]]
                if delta_seconds > 0 and delta_mb >= 0:
                    previous = round(delta_mb * 1024 / delta_seconds, 2)
            result[metric] = _compare_item(summary.get(metric, {}).get("avg"), previous)
    return result


def collect_history(range_key=None, start=None, end=None, interval=None, metrics=None, compare=True):
    """历史趋势主入口：窗口/粒度/指标解析 + 原始点 + 聚合点 + 汇总 + 环比。"""
    from common.models import Monitor

    start_dt, end_dt = resolve_window(range_key, start, end)
    seconds = (end_dt - start_dt).total_seconds()
    interval_key, interval_seconds = resolve_interval(seconds, interval)
    picked = resolve_metrics(metrics)

    # 窗口前最近一条作为速率基准点：窗口起点也能算出网络速率
    base_row = (
        Monitor.objects.filter(created_time__lt=start_dt).order_by("-created_time").values(*_row_fields()).first()
    )
    rows = list(
        Monitor.objects.filter(created_time__gte=start_dt, created_time__lte=end_dt)
        .order_by("created_time")
        .values(*_row_fields())[:ROW_LIMIT]
    )
    raw_points = build_raw_points(base_row, rows)
    summary = summarize_points(raw_points, picked)
    return {
        "range": {
            "start": _iso(start_dt),
            "end": _iso(end_dt),
            "interval": interval_key,
            "interval_seconds": interval_seconds,
            "range_key": range_key or "custom",
        },
        "metrics": picked,
        "points": bucket_points(raw_points, interval_seconds),
        "summary": summary,
        "compare": compare_with_previous(Monitor, start_dt, end_dt, picked, summary) if compare else {},
        "raw_points": len(rows),
    }


def _fmt_time(value):
    """导出用本地时间字符串（points 内为带偏移的本地 ISO 串）。"""
    return str(value)[:19].replace("T", " ")


def build_history_export_sheets(result):
    """历史趋势导出：数据 sheet + 汇总/环比 sheet（CSV/Excel 共用结构）。"""
    metrics = result["metrics"]
    data_header = [str(_("Time"))] + [metric_label(metric) for metric in metrics]
    data_rows = [
        [_fmt_time(point.get("time"))] + [point.get(metric, "") for metric in metrics] for point in result["points"]
    ]
    summary_header = [
        str(_("Metric")),
        str(_("Average")),
        str(_("Minimum")),
        str(_("Maximum")),
        str(_("Latest")),
        str(_("Previous period average")),
        str(_("Change (%)")),
    ]
    summary_rows = []
    for metric in metrics:
        summary = result["summary"].get(metric, {})
        compare = (result.get("compare") or {}).get(metric, {})
        summary_rows.append(
            [
                metric_label(metric),
                summary.get("avg"),
                summary.get("min"),
                summary.get("max"),
                summary.get("last"),
                compare.get("prev_avg"),
                compare.get("percent"),
            ]
        )
    return [
        {"title": str(_("Trend data")), "header": data_header, "rows": data_rows},
        {"title": str(_("Summary")), "header": summary_header, "rows": summary_rows},
    ]
