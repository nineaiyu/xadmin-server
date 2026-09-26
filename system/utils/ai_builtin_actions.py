#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 内置专用动作实现（请假申请 / 动态表单提交 / 首页统计聚合）。

从 ``ai_actions.py`` 拆出仅因行数门禁（500 行）：这些动作是「手写
校验 + 执行」的专用动作（其余能力一律走 ``ai_api_actions.api_action`` 声明式
复用既有业务接口），搬移后 ``ai_actions.ACTION_SPECS`` 引用本模块的函数，
导入方向保持单向（ai_actions → ai_builtin_actions）。
"""

import datetime
import json
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)


def _limits():
    """延迟读取常量：模块加载期不 import ai_actions（双向依赖会循环）。"""
    from system.utils.ai_actions import MAX_CATALOG_FORMS, MAX_REASON_LENGTH

    return MAX_CATALOG_FORMS, MAX_REASON_LENGTH


logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 动作实现：请假申请
# ---------------------------------------------------------------------------


def _parse_date(value):
    try:
        return datetime.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _parse_decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def available_forms(user) -> list:
    """可提交的动态表单（启用中，目录按创建时间倒序取前 N 个）。"""
    from system.models.dform import DynamicForm

    limit, __ = _limits()
    return list(DynamicForm.objects.filter(is_active=True).order_by("-created_time")[:limit])


def _form_fields(form) -> list:
    fields = (form.schema or {}).get("fields") or []
    return [
        {
            "key": str(item.get("key")),
            "label": str(item.get("label")),
            "type": str(item.get("type")),
            "required": bool(item.get("required")),
            "options": item.get("options") or None,
        }
        for item in fields
    ]


def _validate_leave(user, params: dict):
    """校验请假参数：返回 (JSON 安全的规范化参数, 错误文案)。"""
    from approval.models.leave import Leave
    from approval.utils.leave import leave_days, validate_leave_payload

    allowed_types = {choice[0] for choice in Leave.LeaveType.choices}
    leave_type = str(params.get("leave_type") or Leave.LeaveType.ANNUAL).strip()
    if leave_type not in allowed_types:
        return {}, str(_("Unknown leave type: {}").format(leave_type))
    start_date = _parse_date(params.get("start_date"))
    end_date = _parse_date(params.get("end_date"))
    if not start_date or not end_date:
        return {}, str(_("Start date and end date must be in YYYY-MM-DD format"))
    __, reason_limit = _limits()
    reason = str(params.get("reason") or "").strip()[:reason_limit]
    if not reason:
        return {}, str(_("Reason is required"))
    days = None
    raw_days = params.get("days")
    if raw_days not in (None, ""):
        days = _parse_decimal(raw_days)
        if days is None:
            return {}, str(_("Days must be a number"))
    error = validate_leave_payload(start_date=start_date, end_date=end_date, days=days, creator=user)
    if error:
        return {}, error
    if days is None:
        days = leave_days(start_date, end_date)
    return (
        {
            "leave_type": leave_type,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "days": float(days),
            "reason": reason,
        },
        None,
    )


def _execute_leave(user, params: dict) -> dict:
    """创建请假单并立即提交审批（与 LeaveViewSet.create 同口径：无流程/无审批人时保留草稿）。"""
    from approval.models.leave import Leave
    from approval.utils.leave import submit_leave

    leave = Leave.objects.create(
        leave_type=params["leave_type"],
        start_date=_parse_date(params["start_date"]),
        end_date=_parse_date(params["end_date"]),
        days=_parse_decimal(params["days"]),
        reason=params["reason"],
        creator=user,
        modifier=user,
        dept_belong=getattr(user, "dept", None),
    )
    ok, error = submit_leave(leave, user)
    leave.refresh_from_db()
    data = {"leave_id": str(leave.pk), "status": leave.status}
    if not ok:
        return {"ok": True, "detail": str(_("Saved as draft: {}").format(error)), "data": data}
    return {"ok": True, "detail": str(_("The leave request has been submitted for approval")), "data": data}


# ---------------------------------------------------------------------------
# 动作实现：动态表单提交
# ---------------------------------------------------------------------------


def _resolve_form(params: dict):
    """按 form_id 取启用中的表单；返回 (form, 错误文案)。"""
    from system.models.dform import DynamicForm

    form_id = str(params.get("form_id") or "").strip()
    if not form_id:
        return None, str(_("A form must be selected"))
    try:
        form = DynamicForm.objects.filter(pk=form_id).first()
    except (DjangoValidationError, ValueError, TypeError):
        return None, str(_("The requested form does not exist"))
    if form is None:
        return None, str(_("The requested form does not exist"))
    if not form.is_active:
        return None, str(_("The form is disabled and cannot accept submissions"))
    return form, None


def _validate_dform(user, params: dict):
    """校验动态表单提交参数：返回 (JSON 安全的规范化参数, 错误文案)。"""
    from system.utils.dform import validate_submission_data

    form, error = _resolve_form(params)
    if error:
        return {}, error
    data = params.get("data")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return {}, str(_("Submission data must be an object"))
    try:
        normalized = validate_submission_data(form.schema, data)
    except DjangoValidationError as exc:
        return {}, "; ".join(exc.messages)
    return {"form_id": str(form.pk), "data": normalized, "form_name": form.name}, None


def _dform_requires_approval(user, params: dict) -> bool:
    if getattr(user, "is_superuser", False):
        return False
    form, error = _resolve_form(params)
    return bool(form is not None and form.approval_required)


def _execute_dform(user, params: dict) -> dict:
    from system.models.dform import DynamicFormSubmission

    form, error = _resolve_form(params)
    if error:
        return {"ok": False, "detail": error, "data": {}}
    submission = DynamicFormSubmission.objects.create(
        form=form,
        data=params.get("data") or {},
        creator=user,
        modifier=user,
        dept_belong=getattr(user, "dept", None),
    )
    return {
        "ok": True,
        "detail": str(_("The form has been submitted")),
        "data": {"submission_id": str(submission.pk), "form_id": str(form.pk), "form_name": form.name},
    }


# ---------------------------------------------------------------------------
# 动作实现：首页统计聚合（dashboard.overview）
# ---------------------------------------------------------------------------

#: 首页统计端点清单（名称 → 业务路径）；一次调用合并返回，避免模型连发 6 个细粒度查询
DASHBOARD_ENDPOINTS: tuple = (
    ("user_login_total", "/api/system/dashboard/user-login-total"),
    ("user_total", "/api/system/dashboard/user-total"),
    ("user_registered_trend", "/api/system/dashboard/user-registered-trend"),
    ("user_login_trend", "/api/system/dashboard/user-login-trend"),
    ("today_operate_total", "/api/system/dashboard/today-operate-total"),
    ("user_active", "/api/system/dashboard/user-active"),
)

#: 指标可读名（随请求语言翻译，进结果表格「指标」列）
DASHBOARD_LABELS: dict = {
    "user_login_total": _("User logins"),
    "user_total": _("Total users"),
    "user_registered_trend": _("Registrations"),
    "user_login_trend": _("Login trend"),
    "today_operate_total": _("Today's operations"),
    "user_active": _("Active users"),
}


def _validate_dashboard(user, params: dict):
    """无参数动作：原样通过。"""
    return {}, None


def _extract_metric(payload: dict):
    """统计端点响应 → 指标数据。

    两类返回口径（均视为成功后提取）：
    - ``ApiResponse(results=..., percent=..., count=...)``：kwargs 并入响应**顶层**，
      ``data`` 缺省——按顶层键收敛；
    - ``ApiResponse(data=...)``：数据本体在 ``data``。
    """
    if payload.get("data") is not None:
        return payload["data"]
    return {key: payload[key] for key in ("results", "percent", "count") if key in payload}


def _response_payload(response):
    """内部 dispatch 响应 → 统一 JSON payload。

    两类响应形态：DRF Response（``.data``）与 ``cache_response`` 缓存命中时返回的
    HttpResponse（渲染后的 JSON 字符串，无 ``.data``）——首页即 dashboard，登录后
    60s 内执行本动作必然命中缓存，后者是常态路径而非例外。
    """
    payload = getattr(response, "data", None)
    if payload is None:
        try:
            payload = json.loads(response.content)
        except (AttributeError, ValueError):
            return {}
    return payload if isinstance(payload, dict) else {}


def _trend_text(trend) -> str:
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


def _metric_row(cols: list, label, metric) -> dict:
    """单个指标 → 结果表格行。

    行字典直接以翻译后的列名为 key（AiResultTable 的通用契约：columns 即行键）。
    """
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


def _execute_dashboard(user, params: dict) -> dict:
    """内部 dispatch 6 个统计端点并合并（与声明式动作同一内部构造请求口径）。

    data 同时给两种消费形态：``columns/rows/total``（前端 AiResultTable 直接渲染，
    列名随请求语言翻译）与 ``metrics`` 原始指标（MCP/二开脚本消费）。
    单个端点失败不阻断整体（结果里标注 failed），全部失败才报失败。
    """
    from django.urls import Resolver404, resolve
    from rest_framework.test import APIRequestFactory

    factory = APIRequestFactory()
    metrics: dict = {}
    failed: list = []
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
        if isinstance(payload, dict) and payload.get("code") == 1000:
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
