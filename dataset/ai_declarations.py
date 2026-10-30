#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""数据分析与表单采集域的 AI 动作声明（数据集查询 / 动态表单提交）。

- 数据集动作复用既有 execute/aggregate 接口（行级数据权限随调用者 fail-closed）；
- ``dform.submit`` 是内置动作：校验与写入直接复用本应用的
  ``validate_submission_data`` / ``DynamicFormSubmission``，不再由 AI 应用
  重写一份业务规则（历史上 AI 侧复制实现，业务改校验会静默漂移）；
- 表单目录（可选表单与字段）由声明的 ``catalog`` 回调提供，AI 应用不感知业务模型。
"""

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

from ai.services.actions.ai_action_types import MAX_CATALOG_FORMS, ActionSpec
from ai.services.actions.ai_api_actions import IN_QUERY, api_action


def available_forms(user: Any) -> list[Any]:
    """可提交的动态表单（启用中，目录按创建时间倒序取前 N 个）。"""
    from dataset.contracts import DynamicForm

    return list(DynamicForm.objects.filter(is_active=True).order_by("-created_time")[:MAX_CATALOG_FORMS])


def form_fields(form: Any) -> list[Any]:
    """表单 schema → 供模型选择的字段清单（不含服务端校验细节）。"""
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


def _catalog(user: Any) -> dict[str, Any]:
    """目录附加信息：可选表单及其字段（供模型选择 form_id 与填值）。"""
    return {
        "forms": [
            {"form_id": str(form.pk), "name": form.name, "fields": form_fields(form)} for form in available_forms(user)
        ]
    }


def _resolve_form(params: dict[str, Any]) -> Any:
    """按 form_id 取启用中的表单；返回 (form, 错误文案)。"""
    from dataset.contracts import DynamicForm

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


def _validate_dform(user: Any, params: dict[str, Any]) -> Any:
    """校验动态表单提交参数：返回 (JSON 安全的规范化参数, 错误文案)。"""
    from dataset.contracts import validate_submission_data

    form, error = _resolve_form(params)
    if error:
        return {}, error
    data = params.get("data")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return {}, str(_("Submission data must be an object"))
    try:
        normalized = validate_submission_data(form.schema, data, user=user)
    except DjangoValidationError as exc:
        return {}, "; ".join(exc.messages)
    return {"form_id": str(form.pk), "data": normalized, "form_name": form.name}, None


def _dform_requires_approval(user: Any, params: dict[str, Any]) -> bool:
    if getattr(user, "is_superuser", False):
        return False
    form, error = _resolve_form(params)
    return bool(form is not None and form.approval_required)


def _execute_dform(user: Any, params: dict[str, Any]) -> dict[str, Any]:
    from dataset.contracts import DynamicFormSubmission
    from dataset.utils.dform_filter import build_filter_data

    form, error = _resolve_form(params)
    if error:
        return {"ok": False, "detail": error, "data": {}}
    data = params.get("data") or {}
    submission = DynamicFormSubmission.objects.create(
        form=form,
        data=data,
        # 与 REST 提交同口径物化可筛选字段：缺这一步时 AI 提交的行在管理端
        # 「表单数据」按可筛选字段查不到（filter_data @> 不命中），直到重建命令跑过
        filter_data=build_filter_data(form.schema, data),
        creator=user,
        modifier=user,
        dept_belong=getattr(user, "dept", None),
    )
    return {
        "ok": True,
        "detail": str(_("The form has been submitted")),
        "data": {"submission_id": str(submission.pk), "form_id": str(form.pk), "form_name": form.name},
    }


AI_ACTIONS = {
    # ---- 数据集 ----
    "dataset.list": api_action(
        key="dataset.list",
        label=_("List datasets"),
        description=_("List datasets available for analysis (id, name, source model)"),
        method="GET",
        path="/api/dataset/datasets",
        params={"name": {"type": "string", "required": False, "in": IN_QUERY, "description": "Name keyword (fuzzy)"}},
        modules=("analysis",),
    ),
    "dataset.meta": api_action(
        key="dataset.meta",
        label=_("Show dataset designer metadata"),
        description=_("Whitelisted models and fields usable in dataset queries and filters"),
        method="GET",
        path="/api/dataset/datasets/meta",
        params={},
        modules=("analysis",),
    ),
    "dataset.execute": api_action(
        key="dataset.execute",
        label=_("Run a dataset query"),
        description=_("Execute a dataset and return row data (row-level data permissions of the caller apply)"),
        method="POST",
        path="/api/dataset/datasets/<pk>/execute",
        params={"pk": {"type": "pk", "required": True, "in": "path", "description": "Dataset id (from dataset.list)"}},
        modules=("analysis",),
    ),
    "dataset.aggregate": api_action(
        key="dataset.aggregate",
        label=_("Aggregate a dataset"),
        description=_(
            "Aggregate a dataset into series (for charts): group by a field with a metric "
            "(count/sum/avg + value field), optionally truncated by day/month"
        ),
        method="POST",
        path="/api/dataset/datasets/<pk>/aggregate",
        params={
            "pk": {"type": "pk", "required": True, "in": "path", "description": "Dataset id (from dataset.list)"},
            "group_by": {"type": "string", "required": False, "in": "body", "description": "Field to group by"},
            "metric": {
                "type": "string",
                "required": False,
                "in": "body",
                "description": "Aggregate metric: count (default) / sum / avg",
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
                "description": "Date truncation for time grouping: day / month",
            },
        },
        modules=("analysis",),
    ),
    "dataset.detail": api_action(
        key="dataset.detail",
        label=_("Show a dataset"),
        description=_("One dataset definition (bound model, columns, filters, visibility)"),
        method="GET",
        path="/api/dataset/datasets/<pk>",
        params={"pk": {"type": "pk", "required": True, "in": "path", "description": "Dataset id (from dataset.list)"}},
        modules=("analysis",),
    ),
    # ---- 仪表盘 / 报表 / 大屏（只读目录） ----
    "dashboard.list": api_action(
        key="dashboard.list",
        label=_("List dashboards"),
        description=_("Dashboards available to the caller (name, visibility, updated time)"),
        method="GET",
        path="/api/dataset/dashboards",
        params={},
        modules=("analysis",),
    ),
    "report.list": api_action(
        key="report.list",
        label=_("List scheduled reports"),
        description=_("Scheduled reports (name, dataset, frequency, last status)"),
        method="GET",
        path="/api/dataset/reports",
        params={},
        modules=("analysis",),
    ),
    "screen.list": api_action(
        key="screen.list",
        label=_("List data screens"),
        description=_("Data screens (name, visibility, refresh interval, updated time)"),
        method="GET",
        path="/api/dataset/screens",
        params={},
        modules=("analysis",),
    ),
    # ---- 表单采集（只读目录） ----
    "dform.list": api_action(
        key="dform.list",
        label=_("List dynamic forms"),
        description=_("Dynamic forms (name, enabled state, approval required, updated time)"),
        method="GET",
        path="/api/dataset/dynamic-forms",
        params={},
        modules=("dform",),
    ),
    "dform.submission_list": api_action(
        key="dform.submission_list",
        label=_("List dynamic form submissions"),
        description=_("Submissions of dynamic forms visible to the caller (form, creator, created time)"),
        method="GET",
        path="/api/dataset/dynamic-form-submissions",
        params={},
        modules=("dform",),
    ),
    # ---- 动态表单提交（内置动作：校验/写入在本应用内） ----
    "dform.submit": ActionSpec(
        key="dform.submit",
        label=_("Submit a dynamic form"),
        description=_("Submit one record to an active dynamic form; field values must match the form fields"),
        params={
            "form_id": {"type": "string", "required": True, "description": "Target form id from the catalog"},
            "data": {
                "type": "object",
                "required": True,
                "description": "Field key -> value, keys from the form fields",
            },
        },
        required_visits=(("POST", "/api/dataset/dynamic-form-submissions"),),
        validate=_validate_dform,
        execute=_execute_dform,
        requires_approval=_dform_requires_approval,
        available=lambda user: bool(available_forms(user)),
        catalog=_catalog,
        modules=("dform",),
    ),
}
