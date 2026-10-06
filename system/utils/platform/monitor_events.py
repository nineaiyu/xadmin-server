#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""监控告警记录与事件查询（日志与事件记录面板的数据源）。

三类事件：
- alert：资源告警记录（common.MonitorAlert 状态跃迁流水，firing/resolved）；
- error：异常请求（OperationLog 业务码非 1000，与操作日志页 error_status 同口径）；
- task：任务失败（TaskExecution FAILURE/REVOKED，与任务健康度互补的明细视图）。
"""

import datetime

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.core.response import API_SUCCESS_CODE
from common.utils import get_logger

logger = get_logger(__name__)

# 事件查询时间范围（键 → 秒）
EVENT_RANGES = {"1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000}
DEFAULT_EVENT_RANGE = "24h"
ALERT_LIMIT = 200
EVENT_LIMIT = 50
# 分页参数上限：防止一次请求拉全表；超出按该值收敛，截断在响应中如实标注
MAX_PAGE_LIMIT = 1000
# 报表导出按块循环取数：导出全量（不静默截断）且单次查询行数有界
EXPORT_CHUNK_SIZE = 1000
ALERT_ITEMS = ("cpu_percent", "cpu_load", "memory_used", "disk_used")


def resolve_hours(range_key, default_key=DEFAULT_EVENT_RANGE):
    seconds = EVENT_RANGES.get(range_key, EVENT_RANGES[default_key])
    return seconds / 3600


def _page_params(limit, offset, default):
    """分页参数收敛：非法/非正数回退默认页大小，limit 封顶，offset 非负。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = default
    if limit <= 0:
        limit = default
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    return min(limit, MAX_PAGE_LIMIT), offset


def _paged_result(queryset, values_fields, order_by, limit, offset):
    """通用分页取数：返回 results/total/truncated，截断不再静默。"""
    rows = list(queryset.order_by(*order_by).values(*values_fields)[offset : offset + limit])
    total = queryset.count()
    return {"results": rows, "total": total, "truncated": offset + len(rows) < total}


def alert_counts():
    """告警计数（面板角标）：未恢复数 / 24h 新增与恢复数。"""
    from system.models import MonitorAlert

    now = timezone.now()
    day_ago = now - datetime.timedelta(hours=24)
    return {
        "firing": MonitorAlert.objects.filter(status=MonitorAlert.Status.FIRING).count(),
        "total_24h": MonitorAlert.objects.filter(last_time__gte=day_ago).count(),
        "resolved_24h": MonitorAlert.objects.filter(
            status=MonitorAlert.Status.RESOLVED, resolved_time__gte=day_ago
        ).count(),
    }


def collect_alerts(status=None, item=None, range_key="7d", limit=ALERT_LIMIT, offset=0):
    """告警记录查询（默认近 7 天，按最近命中时间倒序；total/truncated 支撑分页）。"""
    from system.models import MonitorAlert

    deadline = timezone.now() - datetime.timedelta(seconds=EVENT_RANGES.get(range_key, 604800))
    queryset = MonitorAlert.objects.filter(last_time__gte=deadline)
    if status in MonitorAlert.Status.values:
        queryset = queryset.filter(status=status)
    if item in ALERT_ITEMS:
        queryset = queryset.filter(item=item)
    limit, offset = _page_params(limit, offset, ALERT_LIMIT)
    data = _paged_result(
        queryset,
        (
            "pk",
            "item",
            "status",
            "value",
            "threshold",
            "message",
            "count",
            "first_time",
            "last_time",
            "resolved_time",
        ),
        ("-last_time", "-pk"),
        limit,
        offset,
    )
    data["counts"] = alert_counts()
    return data


def collect_error_events(range_key=DEFAULT_EVENT_RANGE, limit=EVENT_LIMIT, offset=0):
    """异常请求：业务码非 1000 的操作日志（慢请求另有独立面板）。"""
    from audit.models.log import OperationLog

    deadline = timezone.now() - datetime.timedelta(seconds=EVENT_RANGES.get(range_key, 86400))
    queryset = OperationLog.objects.filter(created_time__gte=deadline).exclude(status_code=API_SUCCESS_CODE)
    limit, offset = _page_params(limit, offset, EVENT_LIMIT)
    return _paged_result(
        queryset,
        (
            "pk",
            "module",
            "path",
            "method",
            "status_code",
            "response_code",
            "exec_time",
            "ipaddress",
            "creator__username",
            "created_time",
        ),
        ("-created_time", "-pk"),
        limit,
        offset,
    )


def collect_task_events(range_key=DEFAULT_EVENT_RANGE, limit=EVENT_LIMIT, offset=0):
    """任务失败事件（FAILURE / REVOKED 终态明细）。"""
    from task.services import TaskExecution

    deadline = timezone.now() - datetime.timedelta(seconds=EVENT_RANGES.get(range_key, 86400))
    queryset = TaskExecution.objects.filter(
        date_finished__gte=deadline,
        status__in=[TaskExecution.Status.FAILURE, TaskExecution.Status.REVOKED],
    )
    limit, offset = _page_params(limit, offset, EVENT_LIMIT)
    return _paged_result(
        queryset,
        ("pk", "name", "status", "date_start", "date_finished"),
        ("-date_finished", "-pk"),
        limit,
        offset,
    )


def collect_events(kind="alert", range_key=DEFAULT_EVENT_RANGE, **kwargs):
    """统一事件入口：kind=alert|error|task；limit/offset 分页，响应带 total/truncated。"""
    limit = kwargs.get("limit")
    offset = kwargs.get("offset") or 0
    if kind == "error":
        return collect_error_events(range_key=range_key, limit=limit, offset=offset)
    if kind == "task":
        return collect_task_events(range_key=range_key, limit=limit, offset=offset)
    return collect_alerts(
        status=kwargs.get("status"), item=kwargs.get("item"), range_key=range_key, limit=limit, offset=offset
    )


def _fmt_time(value):
    if not value:
        return ""
    if isinstance(value, datetime.datetime):
        if timezone.is_aware(value):
            return timezone.localtime(value).strftime("%Y-%m-%d %H:%M:%S")
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)[:19].replace("T", " ")


def build_alert_export_sheets(rows):
    """告警记录导出（CSV/Excel 共用的表格结构）。"""
    from system.models import MonitorAlert

    item_labels = dict(MonitorAlert.Item.choices)
    status_labels = dict(MonitorAlert.Status.choices)
    header = [
        str(_("Alert item")),
        str(_("Status")),
        str(_("Trigger value")),
        str(_("Threshold")),
        str(_("Hit count")),
        str(_("First time")),
        str(_("Last time")),
        str(_("Resolved time")),
        str(_("Message")),
    ]
    export_rows = [
        [
            str(item_labels.get(row["item"], row["item"])),
            str(status_labels.get(row["status"], row["status"])),
            row["value"],
            row["threshold"],
            row["count"],
            _fmt_time(row["first_time"]),
            _fmt_time(row["last_time"]),
            _fmt_time(row["resolved_time"]),
            row["message"],
        ]
        for row in rows
    ]
    return [{"title": str(_("Monitor alerts")), "header": header, "rows": export_rows}]
