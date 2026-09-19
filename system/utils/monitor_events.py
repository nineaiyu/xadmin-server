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

from common.utils import get_logger

logger = get_logger(__name__)

# 事件查询时间范围（键 → 秒）
EVENT_RANGES = {"1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000}
DEFAULT_EVENT_RANGE = "24h"
ALERT_LIMIT = 200
EVENT_LIMIT = 50
ALERT_ITEMS = ("cpu_percent", "cpu_load", "memory_used", "disk_used")


def resolve_hours(range_key, default_key=DEFAULT_EVENT_RANGE):
    seconds = EVENT_RANGES.get(range_key, EVENT_RANGES[default_key])
    return seconds / 3600


def alert_counts():
    """告警计数（面板角标）：未恢复数 / 24h 新增与恢复数。"""
    from common.models import MonitorAlert

    now = timezone.now()
    day_ago = now - datetime.timedelta(hours=24)
    return {
        "firing": MonitorAlert.objects.filter(status=MonitorAlert.Status.FIRING).count(),
        "total_24h": MonitorAlert.objects.filter(last_time__gte=day_ago).count(),
        "resolved_24h": MonitorAlert.objects.filter(
            status=MonitorAlert.Status.RESOLVED, resolved_time__gte=day_ago
        ).count(),
    }


def collect_alerts(status=None, item=None, range_key="7d", limit=ALERT_LIMIT):
    """告警记录查询（默认近 7 天，按最近命中时间倒序）。"""
    from common.models import MonitorAlert

    deadline = timezone.now() - datetime.timedelta(seconds=EVENT_RANGES.get(range_key, 604800))
    queryset = MonitorAlert.objects.filter(last_time__gte=deadline)
    if status in MonitorAlert.Status.values:
        queryset = queryset.filter(status=status)
    if item in ALERT_ITEMS:
        queryset = queryset.filter(item=item)
    rows = list(
        queryset.order_by("-last_time")[:limit].values(
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
        )
    )
    return {"results": rows, "counts": alert_counts()}


def collect_error_events(range_key=DEFAULT_EVENT_RANGE, limit=EVENT_LIMIT):
    """异常请求：业务码非 1000 的操作日志（慢请求另有独立面板）。"""
    from system.models.log import OperationLog

    deadline = timezone.now() - datetime.timedelta(seconds=EVENT_RANGES.get(range_key, 86400))
    rows = list(
        OperationLog.objects.filter(created_time__gte=deadline)
        .exclude(status_code=1000)
        .order_by("-created_time")
        .values(
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
        )[:limit]
    )
    return {"results": rows}


def collect_task_events(range_key=DEFAULT_EVENT_RANGE, limit=EVENT_LIMIT):
    """任务失败事件（FAILURE / REVOKED 终态明细）。"""
    from system.models.task import TaskExecution

    deadline = timezone.now() - datetime.timedelta(seconds=EVENT_RANGES.get(range_key, 86400))
    rows = list(
        TaskExecution.objects.filter(
            date_finished__gte=deadline,
            status__in=[TaskExecution.Status.FAILURE, TaskExecution.Status.REVOKED],
        )
        .order_by("-date_finished")
        .values("pk", "name", "status", "date_start", "date_finished")[:limit]
    )
    return {"results": rows}


def collect_events(kind="alert", range_key=DEFAULT_EVENT_RANGE, **kwargs):
    """统一事件入口：kind=alert|error|task。"""
    if kind == "error":
        return collect_error_events(range_key=range_key, limit=kwargs.get("limit") or EVENT_LIMIT)
    if kind == "task":
        return collect_task_events(range_key=range_key, limit=kwargs.get("limit") or EVENT_LIMIT)
    return collect_alerts(status=kwargs.get("status"), item=kwargs.get("item"), range_key=range_key)


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
    from common.models import MonitorAlert

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
