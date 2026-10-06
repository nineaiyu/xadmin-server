#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""监控历史趋势：时间范围筛选、时间桶聚合、多指标对比与报表数据组装。

数据源为 common.Monitor 心跳表（30s 一条）。网络指标存的是累计收发量，
速率由相邻采样差分得出（进程/系统重启后计数器归零的负差值按缺失处理，
不伪造 0 值峰值）。分桶与汇总聚合下推数据库：相邻差分用窗口函数在
"窗口前基准行 + 窗口内采样"的全序集合上完成，聚合按时间桶 GROUP BY，
只回传聚合结果，避免保留期内数万行心跳全量拉入内存。
"""

import datetime

from django.db import connections
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


def _metrics_cte(table):
    """窗口采样 CTE：窗口内采样（含单次取数上限）+ 窗口前最近一条基准行。

    网络速率用窗口函数在全序集合上对相邻采样差分（速率 = 累计量差 × 1024 /
    间隔秒），负差值（计数器归零）与时间倒退按缺失处理；基准行只参与差分，
    不进入后续聚合（与原 Python 实现的原始点口径一致）。
    """
    fields = ("created_time",) + DIRECT_METRICS + tuple(RATE_SOURCES.values())
    columns = ", ".join(fields)
    lags = ", ".join(f"LAG({source}) OVER w AS prev_{source}" for source in RATE_SOURCES.values())
    rates = ",\n           ".join(
        f"CASE WHEN prev_time IS NOT NULL AND created_time > prev_time AND {source} >= prev_{source}"
        f" THEN ROUND((({source} - prev_{source}) * 1024"
        f" / EXTRACT(EPOCH FROM (created_time - prev_time)))::numeric, 2) END AS {rate}"
        for rate, source in RATE_SOURCES.items()
    )
    return f"""
WITH base AS (
    SELECT {columns}
    FROM {table}
    WHERE created_time < %(start)s
    ORDER BY created_time DESC
    LIMIT 1
), samples AS (
    (SELECT {columns}
     FROM {table}
     WHERE created_time >= %(start)s AND created_time <= %(end)s
     ORDER BY created_time
     LIMIT %(limit)s)
    UNION ALL
    (SELECT {columns} FROM base)
), diffed AS (
    SELECT {columns},
           LAG(created_time) OVER w AS prev_time,
           {lags}
    FROM samples
    WINDOW w AS (ORDER BY created_time)
), metrics AS (
    SELECT created_time, {", ".join(DIRECT_METRICS)},
           {rates}
    FROM diffed
    WHERE created_time >= %(start)s
)"""


def _bucket_query(table):
    """按时间桶聚合：桶 = epoch 秒对桶宽取整（与前端逐点对齐口径一致）。"""
    avgs = ", ".join(f"AVG({field}) AS {field}" for field in DIRECT_METRICS + tuple(RATE_SOURCES))
    return f"""{_metrics_cte(table)}
SELECT FLOOR(EXTRACT(EPOCH FROM created_time) / %(bucket)s) * %(bucket)s AS bucket_key,
       {avgs}
FROM metrics
GROUP BY bucket_key
ORDER BY bucket_key"""


def _summary_query(table):
    """窗口汇总：每指标 min/max/avg + 最后一个有效值（last），COUNT 为原始采样行数。"""
    columns = []
    for metric in DIRECT_METRICS + tuple(RATE_SOURCES):
        columns.extend(
            [
                f"MIN({metric}) AS {metric}_min",
                f"MAX({metric}) AS {metric}_max",
                f"AVG({metric}) AS {metric}_avg",
                f"(ARRAY_AGG({metric} ORDER BY created_time DESC) FILTER (WHERE {metric} IS NOT NULL))[1]"
                f" AS {metric}_last",
            ]
        )
    return f"""{_metrics_cte(table)}
SELECT COUNT(*) AS row_count,
       {", ".join(columns)}
FROM metrics"""


def _fetch_dicts(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]


def _round_or_none(value):
    return None if value is None else round(float(value), 2)


def _bucket_points(rows, interval_seconds):
    """聚合行 → 点序列（桶时间取桶起点；桶内无有效值的字段不出现）。"""
    points = []
    for row in rows:
        bucket_key = int(row["bucket_key"])
        item = {"time": _iso(datetime.datetime.fromtimestamp(bucket_key, tz=datetime.UTC))}
        for field in DIRECT_METRICS + tuple(RATE_SOURCES):
            if row[field] is not None:
                item[field] = _round_or_none(row[field])
        points.append(item)
    return points


def _summarize(row, metrics):
    """汇总行 → 每指标 min/max/avg/last（last 为窗口内最后一个有效值）。"""
    return {
        metric: {
            "min": _round_or_none(row[f"{metric}_min"]),
            "max": _round_or_none(row[f"{metric}_max"]),
            "avg": _round_or_none(row[f"{metric}_avg"]),
            "last": _round_or_none(row[f"{metric}_last"]),
        }
        for metric in metrics
    }


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
    """历史趋势主入口：窗口/粒度/指标解析 + 分桶聚合 + 汇总 + 环比。

    分桶与汇总聚合下推数据库，只回传聚合结果（桶序列 / 汇总 / 环比），
    环比与报表导出复用同一条聚合路径，不重复拉取原始行。
    """
    from system.models import Monitor

    start_dt, end_dt = resolve_window(range_key, start, end)
    seconds = (end_dt - start_dt).total_seconds()
    interval_key, interval_seconds = resolve_interval(seconds, interval)
    picked = resolve_metrics(metrics)

    using = Monitor.objects.db
    table = connections[using].ops.quote_name(Monitor._meta.db_table)
    params = {"start": start_dt, "end": end_dt, "limit": ROW_LIMIT, "bucket": interval_seconds}
    with connections[using].cursor() as cursor:
        cursor.execute(_bucket_query(table), params)
        points = _bucket_points(_fetch_dicts(cursor), interval_seconds)
        cursor.execute(_summary_query(table), params)
        summary_row = _fetch_dicts(cursor)[0]
    summary = _summarize(summary_row, picked)
    return {
        "range": {
            "start": _iso(start_dt),
            "end": _iso(end_dt),
            "interval": interval_key,
            "interval_seconds": interval_seconds,
            "range_key": range_key or "custom",
        },
        "metrics": picked,
        "points": points,
        "summary": summary,
        "compare": compare_with_previous(Monitor, start_dt, end_dt, picked, summary) if compare else {},
        "raw_points": summary_row["row_count"],
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
