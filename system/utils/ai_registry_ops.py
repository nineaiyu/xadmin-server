#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 声明式动作：运维观测域（监控 / 数据集 / 定时任务）。

从 ``ai_api_registry.py`` 拆出仅因行数门禁：本文件只做声明，机制（``api_action`` /
参数解析 / dispatch 执行 / 权限双门）全部在 ``ai_api_actions.py``，汇总仍以
``ai_api_registry.API_ACTION_SPECS`` 为唯一白名单入口。
"""

from django.utils.translation import gettext_lazy as _

from system.utils.ai_api_actions import IN_QUERY, api_action

# ---------------------------------------------------------------------------
# 监控（/api/system/monitor，全部只读）
# ---------------------------------------------------------------------------

ACTION_MONITOR_OVERVIEW = "monitor.overview"
ACTION_MONITOR_SERVICES = "monitor.services"
ACTION_MONITOR_CELERY = "monitor.celery"
ACTION_MONITOR_TASK_HEALTH = "monitor.task_health"
ACTION_MONITOR_SLOW = "monitor.slow"
ACTION_MONITOR_EVENTS = "monitor.events"
ACTION_MONITOR_THRESHOLDS = "monitor.thresholds"

MONITOR_PATH = "/api/system/monitor"

OPS_ACTION_SPECS = {
    ACTION_MONITOR_OVERVIEW: api_action(
        key=ACTION_MONITOR_OVERVIEW,
        label=_("Show system monitor overview"),
        description=_("Host metrics, trends and health status from the system monitor"),
        method="GET",
        path=f"{MONITOR_PATH}/overview",
        params={},
    ),
    ACTION_MONITOR_SERVICES: api_action(
        key=ACTION_MONITOR_SERVICES,
        label=_("Check service health"),
        description=_("Health of DB / Redis / Celery services from the system monitor"),
        method="GET",
        path=f"{MONITOR_PATH}/services",
        params={},
    ),
    ACTION_MONITOR_CELERY: api_action(
        key=ACTION_MONITOR_CELERY,
        label=_("Show Celery workers and queues"),
        description=_("Celery worker status and queue backlog from the system monitor"),
        method="GET",
        path=f"{MONITOR_PATH}/celery",
        params={},
    ),
    ACTION_MONITOR_TASK_HEALTH: api_action(
        key=ACTION_MONITOR_TASK_HEALTH,
        label=_("Show task health"),
        description=_("Background task execution health (success rate, failures) from the system monitor"),
        method="GET",
        path=f"{MONITOR_PATH}/task-health",
        params={},
    ),
    ACTION_MONITOR_SLOW: api_action(
        key=ACTION_MONITOR_SLOW,
        label=_("Show slow requests"),
        description=_("Top slow HTTP requests from the system monitor"),
        method="GET",
        path=f"{MONITOR_PATH}/slow",
        params={},
    ),
    ACTION_MONITOR_EVENTS: api_action(
        key=ACTION_MONITOR_EVENTS,
        label=_("Show monitor alerts and failed requests"),
        description=_("Recent monitor alerts, abnormal requests and failed tasks from the system monitor"),
        method="GET",
        path=f"{MONITOR_PATH}/events",
        params={},
    ),
    ACTION_MONITOR_THRESHOLDS: api_action(
        key=ACTION_MONITOR_THRESHOLDS,
        label=_("Show alert thresholds"),
        description=_("Current monitor alert thresholds (read-only; use the monitor page to change)"),
        method="GET",
        path=f"{MONITOR_PATH}/thresholds",
        params={},
    ),
    # ---- 数据集与报表（数据分析；行级数据权限随调用者过滤，fail-closed） ----
    "dataset.list": api_action(
        key="dataset.list",
        label=_("List datasets"),
        description=_("List datasets available for analysis (id, name, source model)"),
        method="GET",
        path="/api/system/datasets",
        params={"name": {"type": "string", "required": False, "in": IN_QUERY, "description": "Name keyword (fuzzy)"}},
    ),
    "dataset.meta": api_action(
        key="dataset.meta",
        label=_("Show dataset designer metadata"),
        description=_("Whitelisted models and fields usable in dataset queries and filters"),
        method="GET",
        path="/api/system/datasets/meta",
        params={},
    ),
    "dataset.execute": api_action(
        key="dataset.execute",
        label=_("Run a dataset query"),
        description=_("Execute a dataset and return row data (row-level data permissions of the caller apply)"),
        method="POST",
        path="/api/system/datasets/<pk>/execute",
        params={"pk": {"type": "pk", "required": True, "in": "path", "description": "Dataset id (from dataset.list)"}},
    ),
    "dataset.aggregate": api_action(
        key="dataset.aggregate",
        label=_("Aggregate a dataset"),
        description=_(
            "Aggregate a dataset into series (for charts): group by a field with a metric "
            "(count/sum/avg/max/min + value field), optionally truncated by date"
        ),
        method="POST",
        path="/api/system/datasets/<pk>/aggregate",
        params={
            "pk": {"type": "pk", "required": True, "in": "path", "description": "Dataset id (from dataset.list)"},
            "group_by": {"type": "string", "required": False, "in": "body", "description": "Field to group by"},
            "metric": {
                "type": "string",
                "required": False,
                "in": "body",
                "description": "Aggregate metric: count (default) / sum / avg / max / min",
            },
            "value_field": {
                "type": "string",
                "required": False,
                "in": "body",
                "description": "Numeric field the metric applies to (not needed for count)",
            },
            "date_trunc": {
                "type": "string",
                "required": False,
                "in": "body",
                "description": "Date truncation for time grouping: day / month / year",
            },
        },
    ),
    # ---- 定时任务（查询 + 启停 + 立即执行） ----
    "task.list": api_action(
        key="task.list",
        label=_("List scheduled tasks"),
        description=_("List periodic tasks with schedule, enabled state and last run"),
        method="GET",
        path="/api/system/tasks/periodic",
        params={"name": {"type": "string", "required": False, "in": IN_QUERY, "description": "Task name keyword"}},
    ),
    "task.registered": api_action(
        key="task.registered",
        label=_("List registered task types"),
        description=_("All Celery task types registered in the system (targets for new scheduled tasks)"),
        method="GET",
        path="/api/system/tasks/periodic/registered",
        params={},
    ),
    "task.enable": api_action(
        key="task.enable",
        label=_("Enable or disable a scheduled task"),
        description=_("Set a periodic task's enabled state; omit the parameter to toggle it"),
        # 业务端点为 PATCH（views/task.py::PeriodicTaskViewSet.enable），声明必须同方法，
        # 否则内部 dispatch 405（且 POST 权限点不存在 → 普通用户直接被预检拦掉）。
        method="PATCH",
        path="/api/system/tasks/periodic/<pk>/enable",
        params={
            "pk": {"type": "pk", "required": True, "in": "path", "description": "Task id (from task.list)"},
            "enabled": {
                "type": "bool",
                "required": False,
                "in": "body",
                "description": "True enable, False disable; omit to toggle",
            },
        },
    ),
    "task.run": api_action(
        key="task.run",
        label=_("Run a scheduled task now"),
        description=_("Trigger one immediate run of a periodic task and return the execution id"),
        method="POST",
        path="/api/system/tasks/periodic/<pk>/run",
        params={"pk": {"type": "pk", "required": True, "in": "path", "description": "Task id (from task.list)"}},
    ),
    "task.executions": api_action(
        key="task.executions",
        label=_("List task execution history"),
        description=_("History of scheduled task runs (status, duration, timing)"),
        method="GET",
        path="/api/system/tasks/executions",
        params={
            "status": {
                "type": "string",
                "required": False,
                "in": IN_QUERY,
                "description": "Filter by status keyword",
            }
        },
    ),
    "task.execution_log": api_action(
        key="task.execution_log",
        label=_("Show task execution log"),
        description=_("Log output of one task execution"),
        method="GET",
        path="/api/system/tasks/executions/<pk>/log",
        params={
            "pk": {"type": "pk", "required": True, "in": "path", "description": "Execution id (from task.executions)"}
        },
    ),
}
