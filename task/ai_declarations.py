#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""任务域的 AI 动作声明（定时任务 / 执行历史 / 导出记录 / Webhook 订阅）。

- 定时任务与导出记录随 ``ops`` 模块裁剪；
- Webhook 订阅同时属于 ``ops`` 与 ``webhook`` 两个模块，声明两个模块后
  「全部启用」才进目录（跨模块端点任一侧停用都不可达）。
"""

from django.utils.translation import gettext_lazy as _

from ai.services.actions.ai_api_actions import IN_QUERY, api_action

AI_ACTIONS = {
    # ---- 定时任务 ----
    "task.list": api_action(
        key="task.list",
        label=_("List scheduled tasks"),
        description=_("List periodic tasks with schedule, enabled state and last run"),
        method="GET",
        path="/api/task/periodic",
        params={"name": {"type": "string", "required": False, "in": IN_QUERY, "description": "Task name keyword"}},
        modules=("ops",),
    ),
    "task.registered": api_action(
        key="task.registered",
        label=_("List registered task types"),
        description=_("All Celery task types registered in the system (targets for new scheduled tasks)"),
        method="GET",
        path="/api/task/periodic/registered",
        params={},
        modules=("ops",),
    ),
    "task.enable": api_action(
        key="task.enable",
        label=_("Enable or disable a scheduled task"),
        description=_("Set a periodic task's enabled state; omit the parameter to toggle it"),
        # 业务端点为 PATCH（views/task.py::PeriodicTaskViewSet.enable），声明必须同方法，
        # 否则内部 dispatch 405（且 POST 权限点不存在 → 普通用户直接被预检拦掉）。
        method="PATCH",
        path="/api/task/periodic/<pk>/enable",
        params={
            "pk": {"type": "pk", "required": True, "in": "path", "description": "Task id (from task.list)"},
            "enabled": {
                "type": "bool",
                "required": False,
                "in": "body",
                "description": "True enable, False disable; omit to toggle",
            },
        },
        modules=("ops",),
    ),
    "task.run": api_action(
        key="task.run",
        label=_("Run a scheduled task now"),
        description=_("Trigger one immediate run of a periodic task and return the execution id"),
        method="POST",
        path="/api/task/periodic/<pk>/run",
        params={"pk": {"type": "pk", "required": True, "in": "path", "description": "Task id (from task.list)"}},
        modules=("ops",),
    ),
    "task.executions": api_action(
        key="task.executions",
        label=_("List task execution history"),
        description=_("History of scheduled task runs (status, duration, timing)"),
        method="GET",
        path="/api/task/executions",
        params={
            "status": {
                "type": "string",
                "required": False,
                "in": IN_QUERY,
                "description": "Filter by status keyword",
            }
        },
        modules=("ops",),
    ),
    "task.execution_log": api_action(
        key="task.execution_log",
        label=_("Show task execution log"),
        description=_("Log output of one task execution"),
        method="GET",
        path="/api/task/executions/<pk>/log",
        params={
            "pk": {"type": "pk", "required": True, "in": "path", "description": "Execution id (from task.executions)"}
        },
        modules=("ops",),
    ),
    # ---- 下载中心 ----
    "export.list": api_action(
        key="export.list",
        label=_("List export records"),
        description=_("Async export records in the download center (file, status, creator)"),
        method="GET",
        path="/api/task/exports",
        params={},
        modules=("ops",),
    ),
    # ---- Webhook 订阅 ----
    "webhook.list": api_action(
        key="webhook.list",
        label=_("List webhook subscriptions"),
        description=_("Outbound webhook subscriptions (target URL, subscribed events, enabled state)"),
        method="GET",
        path="/api/task/webhooks/subscriptions",
        params={},
        modules=("ops", "webhook"),
    ),
    "webhook.deliveries": api_action(
        key="webhook.deliveries",
        label=_("List webhook deliveries"),
        description=_("Delivery history of outbound webhooks (event, status, attempts, response code)"),
        method="GET",
        path="/api/task/webhooks/deliveries",
        params={
            "status": {
                "type": "string",
                "required": False,
                "in": IN_QUERY,
                "description": "Filter by delivery status keyword",
            }
        },
        modules=("ops", "webhook"),
    ),
    # ---- 任务中心（跨类型统一视图 + 协作取消 + 白名单重跑） ----
    "task.unified_list": api_action(
        key="task.unified_list",
        label=_("List all background tasks"),
        description=_("Unified task list across executions, exports and imports (type, status, keyword)"),
        method="GET",
        path="/api/task/unified",
        params={
            "type": {
                "type": "string",
                "required": False,
                "in": IN_QUERY,
                "description": "Comma separated types: task / export / import",
            },
            "status": {"type": "string", "required": False, "in": IN_QUERY, "description": "Status keyword"},
            "keyword": {"type": "string", "required": False, "in": IN_QUERY, "description": "Name keyword"},
        },
        modules=("ops",),
    ),
    "task.cancel": api_action(
        key="task.cancel",
        label=_("Cancel a background task"),
        description=_("Cancel a pending task immediately or ask a running task to stop at a safe point"),
        method="POST",
        path="/api/task/unified/cancel",
        params={
            "type": {
                "type": "enum",
                "values": ["task", "export", "import"],
                "required": True,
                "in": "body",
                "description": "Record type (from task.unified_list)",
            },
            "pk": {"type": "pk", "required": True, "in": "body", "description": "Record id (from task.unified_list)"},
        },
        modules=("ops",),
    ),
    "task.rerun": api_action(
        key="task.rerun",
        label=_("Rerun a background task"),
        description=_("Create and dispatch a new run of an export or import record (same pipeline, audited)"),
        method="POST",
        path="/api/task/unified/rerun",
        params={
            "type": {
                "type": "enum",
                "values": ["export", "import"],
                "required": True,
                "in": "body",
                "description": "Record type (from task.unified_list)",
            },
            "pk": {"type": "pk", "required": True, "in": "body", "description": "Record id (from task.unified_list)"},
        },
        modules=("ops",),
    ),
    "export.detail": api_action(
        key="export.detail",
        label=_("Show an export record"),
        description=_("One download-center export record with status, product file and error message"),
        method="GET",
        path="/api/task/exports/<pk>",
        params={
            "pk": {"type": "pk", "required": True, "in": "path", "description": "Export record id (from export.list)"}
        },
        modules=("ops",),
    ),
    "import.list": api_action(
        key="import.list",
        label=_("List import records"),
        description=_("Data import records (template, status, progress, creator)"),
        method="GET",
        path="/api/task/imports",
        params={},
        modules=("ops",),
    ),
}
