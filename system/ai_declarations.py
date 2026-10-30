#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""平台域的 AI 动作声明（系统配置 / 字典 / 菜单与权限 / 运维监控 / 首页统计）。

- 声明式动作复用既有业务接口（零 AI 专用业务代码）；
- ``dashboard.overview`` 是一次调用聚合六个首页统计端点的内置动作，避免模型连发
  细粒度查询；端点清单与聚合逻辑属于平台域，就近维护；
- 监控动作标注 ``modules=("ops",)``：停用运维模块后不再出现在目录、也不可执行。
"""

import json
from typing import Any

from django.utils.translation import gettext_lazy as _

from ai.services.actions.ai_action_types import ActionSpec, always_available, never_requires_approval
from ai.services.actions.ai_api_actions import IN_QUERY, api_action, requires_approval_high_risk
from common.core.response import API_SUCCESS_CODE
from common.utils import get_logger

logger = get_logger(__name__)

#: 首页统计端点清单（名称 → 业务路径）；一次调用合并返回
DASHBOARD_ENDPOINTS: tuple[Any, ...] = (
    ("user_login_total", "/api/system/dashboard/user-login-total"),
    ("user_total", "/api/system/dashboard/user-total"),
    ("user_registered_trend", "/api/system/dashboard/user-registered-trend"),
    ("user_login_trend", "/api/system/dashboard/user-login-trend"),
    ("today_operate_total", "/api/system/dashboard/today-operate-total"),
    ("user_active", "/api/system/dashboard/user-active"),
)

#: 指标可读名（随请求语言翻译，进结果表格「指标」列）
DASHBOARD_LABELS: dict[str, Any] = {
    "user_login_total": _("User logins"),
    "user_total": _("Total users"),
    "user_registered_trend": _("Registrations"),
    "user_login_trend": _("Login trend"),
    "today_operate_total": _("Today's operations"),
    "user_active": _("Active users"),
}

MONITOR_PATH = "/api/system/monitor"


def _validate_dashboard(user: Any, params: Any) -> Any:
    """无参数动作：原样通过。"""
    return {}, None


def _extract_metric(payload: dict[str, Any]) -> Any:
    """统计端点响应 → 指标数据（顶层 results/percent/count 或 data 本体）。"""
    if payload.get("data") is not None:
        return payload["data"]
    return {key: payload[key] for key in ("results", "percent", "count") if key in payload}


def _response_payload(response: Any) -> Any:
    """内部 dispatch 响应 → 统一 JSON payload（兼容缓存命中返回的 HttpResponse）。"""
    payload = getattr(response, "data", None)
    if payload is None:
        try:
            payload = json.loads(response.content)
        except (AttributeError, ValueError):
            return {}
    return payload if isinstance(payload, dict) else {}


def _trend_text(trend: Any) -> str:
    """趋势序列压缩为可读短串：``[{day, count}]`` → ``09-15=3 09-16=5 …``。"""
    if not isinstance(trend, list):
        return ""
    parts = []
    for item in trend[-7:]:
        if isinstance(item, dict):
            parts.append(f"{item.get('day')}={item.get('count')}")
        else:
            parts.append(json.dumps(item, ensure_ascii=False))
    return " ".join(parts)


def _metric_row(cols: list[Any], label: Any, metric: Any) -> dict[str, Any]:
    """单个指标 → 结果表格行（行字典以翻译后的列名为 key）。"""
    count = percent = trend = None
    if isinstance(metric, dict):
        count = metric.get("count")
        percent = metric.get("percent")
        trend = metric.get("results", metric.get("data"))
    elif metric is not None:
        trend = metric
    return {
        cols[0]: str(label),
        cols[1]: str(count) if count is not None else "",
        cols[2]: f"{percent}%" if percent is not None else "",
        cols[3]: _trend_text(trend),
    }


def _execute_dashboard(user: Any, params: Any) -> dict[str, Any]:
    """内部 dispatch 六个统计端点并合并（单个失败不阻断整体）。"""
    from django.urls import Resolver404, resolve
    from rest_framework.test import APIRequestFactory

    factory = APIRequestFactory()
    metrics: dict[str, Any] = {}
    failed: list[Any] = []
    for name, path in DASHBOARD_ENDPOINTS:
        try:
            match = resolve(path)
        except Resolver404:
            logger.warning("dashboard overview path not resolvable. path:%s", path)
            failed.append(name)
            continue
        request = factory.get(path)
        request._force_auth_user = user
        try:
            response = match.func(request)
        except Exception:  # noqa: BLE001 单端点失败不阻断整体聚合
            logger.warning("dashboard overview endpoint failed. path:%s", path, exc_info=True)
            failed.append(name)
            continue
        payload = _response_payload(response)
        if isinstance(payload, dict) and payload.get("code") == API_SUCCESS_CODE:
            metrics[name] = _extract_metric(payload)
        else:
            failed.append(name)
    if not metrics:
        return {"ok": False, "detail": str(_("The action failed: no dashboard metrics available")), "data": {}}
    cols = [str(_("Indicator")), str(_("Value")), str(_("Day-over-day")), str(_("Trend"))]
    rows = [_metric_row(cols, DASHBOARD_LABELS.get(name, name), metric) for name, metric in metrics.items()]
    data = {
        "columns": cols,
        "rows": rows,
        "total": len(rows),
        "metrics": metrics,
        **({"failed": failed} if failed else {}),
    }
    detail = str(_("Dashboard overview compiled"))
    if failed:
        detail = str(_("Dashboard overview compiled (unavailable: {})").format(", ".join(failed)))
    return {"ok": True, "detail": detail, "data": data}


AI_ACTIONS = {
    # ---- 系统配置（config.list 只读；config.set 高危强制审批） ----
    "config.list": api_action(
        key="config.list",
        label=_("List system configurations"),
        description=_("List system config entries (key, value, description); read-only view of platform settings"),
        method="GET",
        path="/api/system/config/system",
        params={"key": {"type": "string", "required": False, "in": IN_QUERY, "description": "Config key keyword"}},
    ),
    "config.set": api_action(
        key="config.set",
        label=_("Update a system configuration value"),
        description=_(
            "Change one system config entry's value (requires approval). Pass the value as JSON text "
            "exactly shaped like the current value from config.list; wrong shape may break the feature"
        ),
        method="PATCH",
        path="/api/system/config/system/<pk>",
        params={
            "pk": {"type": "pk", "required": True, "in": "path", "description": "Config row id (from config.list)"},
            "value": {
                "type": "string",
                "required": True,
                "in": "body",
                "description": "New value as JSON text (same shape as the current value)",
            },
        },
        requires_approval=requires_approval_high_risk,
    ),
    # ---- 数据字典（dict.items 走 PERMISSION_WHITE_URL，无菜单权限点） ----
    "dict.list": api_action(
        key="dict.list",
        label=_("List dictionary types"),
        description=_("List data dictionary types (code, name, item count)"),
        method="GET",
        path="/api/system/dict",
        params={"code": {"type": "string", "required": False, "in": IN_QUERY, "description": "Type code keyword"}},
    ),
    "dict.items": api_action(
        key="dict.items",
        label=_("Show dictionary items"),
        description=_("Enabled items of one dictionary type by its code (labels used across the system)"),
        method="GET",
        path="/api/system/dict/items",
        params={
            "code": {
                "type": "string",
                "required": True,
                "in": IN_QUERY,
                "description": "Dictionary type code (from dict.list)",
            }
        },
    ),
    # ---- 菜单与数据权限（授权讨论用只读视图） ----
    "menu.list": api_action(
        key="menu.list",
        label=_("List menus and permissions"),
        description=_("Menu tree including permission points (for discussing authorization scope)"),
        method="GET",
        path="/api/system/menu",
        params={},
    ),
    "permission.list": api_action(
        key="permission.list",
        label=_("List data permission rules"),
        description=_("Row-level data permission rules (model, scope, bound roles/depts/users)"),
        method="GET",
        path="/api/system/permission",
        params={},
    ),
    # ---- 运维监控（只读；随 ops 模块裁剪） ----
    "monitor.overview": api_action(
        key="monitor.overview",
        label=_("Show system monitor overview"),
        description=_("Host metrics, trends and health status from the system monitor"),
        method="GET",
        path=f"{MONITOR_PATH}/overview",
        params={},
        modules=("ops",),
    ),
    "monitor.services": api_action(
        key="monitor.services",
        label=_("Check service health"),
        description=_("Health of DB / Redis / Celery services from the system monitor"),
        method="GET",
        path=f"{MONITOR_PATH}/services",
        params={},
        modules=("ops",),
    ),
    "monitor.celery": api_action(
        key="monitor.celery",
        label=_("Show Celery workers and queues"),
        description=_("Celery worker status and queue backlog from the system monitor"),
        method="GET",
        path=f"{MONITOR_PATH}/celery",
        params={},
        modules=("ops",),
    ),
    "monitor.task_health": api_action(
        key="monitor.task_health",
        label=_("Show task health"),
        description=_("Background task execution health (success rate, failures) from the system monitor"),
        method="GET",
        path=f"{MONITOR_PATH}/task-health",
        params={},
        modules=("ops",),
    ),
    "monitor.slow": api_action(
        key="monitor.slow",
        label=_("Show slow requests"),
        description=_("Top slow HTTP requests from the system monitor"),
        method="GET",
        path=f"{MONITOR_PATH}/slow",
        params={},
        modules=("ops",),
    ),
    "monitor.events": api_action(
        key="monitor.events",
        label=_("Show monitor alerts and failed requests"),
        description=_("Recent monitor alerts, abnormal requests and failed tasks from the system monitor"),
        method="GET",
        path=f"{MONITOR_PATH}/events",
        params={},
        modules=("ops",),
    ),
    "monitor.thresholds": api_action(
        key="monitor.thresholds",
        label=_("Show alert thresholds"),
        description=_("Current monitor alert thresholds (read-only; use the monitor page to change)"),
        method="GET",
        path=f"{MONITOR_PATH}/thresholds",
        params={},
        modules=("ops",),
    ),
    # ---- 配置目录 / 字段权限 / 模块清单 / 权限巡检（只读） ----
    "config.registered_keys": api_action(
        key="config.registered_keys",
        label=_("List registered config keys"),
        description=_("All registered system config keys with defaults and descriptions (no values)"),
        method="GET",
        path="/api/system/config/system/registered-keys",
        params={},
    ),
    "dict.detail": api_action(
        key="dict.detail",
        label=_("Show a dictionary type"),
        description=_("One data dictionary type with its configuration"),
        method="GET",
        path="/api/system/dict/<pk>",
        params={"pk": {"type": "pk", "required": True, "in": "path", "description": "Dictionary id (from dict.list)"}},
    ),
    "field.list": api_action(
        key="field.list",
        label=_("List field permission entries"),
        description=_("Field-level permission tree entries (model, field, permission type)"),
        method="GET",
        path="/api/system/field",
        params={},
    ),
    "module.list": api_action(
        key="module.list",
        label=_("List feature modules"),
        description=_("Feature modules with level, enabled state and dependency info"),
        method="GET",
        path="/api/system/modules",
        params={},
    ),
    "menu.permission_audit": api_action(
        key="menu.permission_audit",
        label=_("Audit menu permissions"),
        description=_("Permission point audit of menus (missing or unreachable points)"),
        method="GET",
        path="/api/system/menu/permission-audit",
        params={},
    ),
    "monitor.history": api_action(
        key="monitor.history",
        label=_("Show monitor history"),
        description=_("Resource metric history with range and aggregation interval; compare with the previous window"),
        method="GET",
        path=f"{MONITOR_PATH}/history",
        params={
            "range": {
                "type": "enum",
                "values": ["1h", "6h", "24h", "7d", "30d"],
                "default": "24h",
                "in": IN_QUERY,
                "description": "Time range, default 24h",
            },
            "interval": {
                "type": "enum",
                "values": ["auto", "1m", "5m", "15m", "1h", "1d"],
                "default": "auto",
                "in": IN_QUERY,
                "description": "Aggregation interval, default auto",
            },
        },
        modules=("ops",),
    ),
    # ---- 首页统计聚合（内置动作：一次调用合并六个端点） ----
    "dashboard.overview": ActionSpec(
        key="dashboard.overview",
        label=_("Show dashboard overview"),
        description=_(
            "One-shot dashboard statistics: total users, logins, registration/login trends, "
            "today's operations and active users (aggregates six endpoints in a single call)"
        ),
        params={},
        required_visits=tuple(("GET", path) for __, path in DASHBOARD_ENDPOINTS),
        validate=_validate_dashboard,
        execute=_execute_dashboard,
        requires_approval=never_requires_approval,
        available=always_available,
    ),
}
