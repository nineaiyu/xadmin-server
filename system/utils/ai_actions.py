#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""AI 助手受限动作（A2：从「问答」到「权限内执行」）。

红线：
- AI 永不直接执行：LLM 只产出 action draft，必须由用户在确认卡片上二次确认后才执行；
- 白名单动作：动作类型与参数在服务端注册表中逐项校验（LLM 输出按不可信输入处理）；
- 以用户身份执行：creator/申请人恒为发起用户，参数不接受任何「替他人」字段；
  执行前另行校验底层业务权限点（与菜单权限同一匹配口径），双保险防越权；
- 可审计：每次执行落 OperationLog(module=AI:action, auth_type=ai)；
- 默认关闭：SysConfig.AI_ACTION_ENABLED（灰度开关，见 AI 配置页）。

结构：
- ACTION_SPECS：动作注册表（唯一白名单），新增动作 = 新增 ActionSpec；
- build_catalog / build_draft_prompt / parse_draft：LLM 草稿链路（v1 仅请假与动态表单两个动作）；
- user_can_visit：业务权限点判定（复用菜单权限匹配函数，与 IsAuthenticated 同源）。
"""

import datetime
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)

#: prompt 中动作目录的定界标记（同样供 E2E 桩 LLM 解析，勿随意改动格式）
ALLOWED_ACTIONS_MARKER = "ALLOWED_ACTIONS_JSON:"
#: 用户单条请求长度上限（与知识库问答同量级）
MAX_MESSAGE_LENGTH = 500
#: 事由/文本参数上限（与 Leave.reason 字段一致）
MAX_REASON_LENGTH = 500
#: 动态表单目录最多列出的表单数（防 prompt 过大）
MAX_CATALOG_FORMS = 20

ACTION_LEAVE_SUBMIT = "leave.submit"
ACTION_DFORM_SUBMIT = "dform.submit"


def ai_action_enabled() -> bool:
    """AI 动作灰度开关（默认关闭）。"""
    return bool(getattr(settings, "AI_ACTION_ENABLED", False))


def user_can_visit(user, method: str, path: str) -> bool:
    """按菜单权限点口径判定用户能否访问「方法 + 路径」（与 IsAuthenticated 同一匹配函数）。

    业务动作执行前的第二道门：仅有 AI 执行端点权限、而没有底层业务权限的用户不得执行。
    """
    if not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
        return False
    if getattr(user, "is_superuser", False):
        return True
    from common.core.permission import get_menu_pk, get_user_permission

    try:
        permission_data = get_user_permission(user, method.upper())
    except Exception:  # noqa: BLE001 权限查询失败按无权限处理（fail-closed）
        logger.warning("check ai action permission failed. user:%s", getattr(user, "pk", ""), exc_info=True)
        return False
    return bool(permission_data and get_menu_pk(permission_data, path))


def _extract_json_object(text: str) -> dict:
    """robust 解析 LLM 输出：剥 markdown 码栅后取首个 JSON 对象。"""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        text = text[start : end + 1] if (start >= 0 and end > start) else text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DjangoValidationError(_("The model returned malformed JSON")) from exc
    if not isinstance(payload, dict):
        raise DjangoValidationError(_("The model returned malformed JSON"))
    return payload


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


# ---------------------------------------------------------------------------
# 动作实现：请假申请
# ---------------------------------------------------------------------------


def available_forms(user) -> list:
    """可提交的动态表单（启用中，目录按创建时间倒序取前 N 个）。"""
    from system.models.dform import DynamicForm

    return list(DynamicForm.objects.filter(is_active=True).order_by("-created_time")[:MAX_CATALOG_FORMS])


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
    from system.models.leave import Leave
    from system.utils.leave import leave_days, validate_leave_payload

    allowed_types = {choice[0] for choice in Leave.LeaveType.choices}
    leave_type = str(params.get("leave_type") or Leave.LeaveType.ANNUAL).strip()
    if leave_type not in allowed_types:
        return {}, str(_("Unknown leave type: {}").format(leave_type))
    start_date = _parse_date(params.get("start_date"))
    end_date = _parse_date(params.get("end_date"))
    if not start_date or not end_date:
        return {}, str(_("Start date and end date must be in YYYY-MM-DD format"))
    reason = str(params.get("reason") or "").strip()[:MAX_REASON_LENGTH]
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
    from system.models.leave import Leave
    from system.utils.leave import submit_leave

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
# 注册表
# ---------------------------------------------------------------------------


def _never_requires_approval(user, params) -> bool:
    return False


def _always_available(user) -> bool:
    return True


def _dform_available(user) -> bool:
    return bool(available_forms(user))


@dataclass(frozen=True)
class ActionSpec:
    """受限动作规格（白名单条目）。"""

    key: str
    label: object
    description: object
    params: dict
    #: ((method, path), ...)：执行所需底层业务权限点（路径与菜单权限点 path 同口径）
    required_visits: tuple
    validate: object
    execute: object
    requires_approval: object
    available: object

    def has_permission(self, user) -> bool:
        """业务权限 + 可用性双门（执行前与草稿生成前共用）。"""
        return all(user_can_visit(user, method, path) for method, path in self.required_visits) and bool(
            self.available(user)
        )


ACTION_SPECS = {
    ACTION_LEAVE_SUBMIT: ActionSpec(
        key=ACTION_LEAVE_SUBMIT,
        label=_("Submit a leave request"),
        description=_("Create a leave request for the current user and submit it into the leave approval flow"),
        params={
            "leave_type": {
                "type": "string",
                "required": False,
                "enum": ["annual", "sick", "personal", "comp_time", "marriage", "other"],
                "description": "Leave type, default annual",
            },
            "start_date": {"type": "string", "required": True, "description": "Start date, YYYY-MM-DD"},
            "end_date": {"type": "string", "required": True, "description": "End date, YYYY-MM-DD"},
            "days": {"type": "number", "required": False, "description": "Days, default the full range"},
            "reason": {"type": "string", "required": True, "description": "Reason, max 500 chars"},
        },
        required_visits=(("POST", "/api/system/leaves"),),
        validate=_validate_leave,
        execute=_execute_leave,
        requires_approval=_never_requires_approval,
        available=_always_available,
    ),
    ACTION_DFORM_SUBMIT: ActionSpec(
        key=ACTION_DFORM_SUBMIT,
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
        required_visits=(("POST", "/api/system/dynamic-form-submissions"),),
        validate=_validate_dform,
        execute=_execute_dform,
        requires_approval=_dform_requires_approval,
        available=_dform_available,
    ),
}


def get_action(key: str):
    return ACTION_SPECS.get(str(key or "").strip())


def available_actions(user) -> list:
    """当前用户可用的动作（权限 + 可用性双门）。"""
    return [spec for spec in ACTION_SPECS.values() if spec.has_permission(user)]


def build_catalog(user) -> dict:
    """动作目录（进 LLM prompt 的 JSON；不含任何敏感配置）。"""
    forms = available_forms(user)
    entries = []
    for spec in available_actions(user):
        entry = {
            "action": spec.key,
            "label": str(spec.label),
            "description": str(spec.description),
            "params": spec.params,
        }
        if spec.key == ACTION_DFORM_SUBMIT:
            entry["forms"] = [
                {"form_id": str(form.pk), "name": form.name, "fields": _form_fields(form)} for form in forms
            ]
        entries.append(entry)
    return {"actions": entries}


def build_draft_prompt(user, message: str) -> list:
    """构造草稿 prompt：动作目录以标记行定位，便于解析与测试（勿改标记格式）。

    注意：文案含 JSON 花括号，不能用 str.format 注入日期（会被当占位符解析成
    KeyError），这里用 replace 注入 ``{today}``。
    """
    catalog = build_catalog(user)
    system = str(
        _(
            "You convert the user's request into exactly ONE action draft for this system. "
            "Pick the action only from the provided catalog and use only the described parameters. "
            'Output ONLY a JSON object: {"action": "<action key>", "params": {...}, "summary": "<one line>"}. '
            "Never invent values the user did not provide; resolve relative dates with the current date {today}. "
            "If the request is not executable, is missing required parameters, or matches several forms, "
            'output {"action": null, "message": "<a short clarifying question>"}.'
        )
    ).replace("{today}", datetime.date.today().isoformat())
    user_content = "{}\n\n{}\n{}".format(
        (message or "").strip()[:MAX_MESSAGE_LENGTH],
        ALLOWED_ACTIONS_MARKER,
        json.dumps(catalog, ensure_ascii=False),
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user_content}]


def parse_draft(raw: str, user) -> dict:
    """解析 LLM 草稿输出并逐项服务端校验（LLM 输出按不可信输入处理）。

    返回 ``{"kind": "draft", "draft": {...}}`` 或 ``{"kind": "message", "message": "..."}``；
    校验不通过抛 DjangoValidationError（可读文案，前端按普通业务失败提示）。
    """
    payload = _extract_json_object(raw)
    action_key = payload.get("action")
    if not action_key:
        message = str(payload.get("message") or "").strip()[:MAX_REASON_LENGTH]
        if not message:
            raise DjangoValidationError(_("The model did not return an actionable request"))
        return {"kind": "message", "message": message}

    spec = get_action(action_key)
    if spec is None:
        raise DjangoValidationError(_("The model requested an unknown action: {}").format(str(action_key)[:64]))
    if not spec.available(user):
        raise DjangoValidationError(_("The action is not available: {}").format(str(spec.label)))
    if not spec.has_permission(user):
        raise DjangoValidationError(_("You do not have permission to perform the action: {}").format(str(spec.label)))

    params = payload.get("params")
    clean, error = spec.validate(user, params if isinstance(params, dict) else {})
    if error:
        raise DjangoValidationError(error)
    draft = {
        "action": spec.key,
        "label": str(spec.label),
        "params": clean,
        "summary": str(payload.get("summary") or "").strip()[:200] or str(spec.label),
        "requires_approval": bool(spec.requires_approval(user, clean)),
    }
    return {"kind": "draft", "draft": draft}


def audit_ai_action(user, action_key: str, params, ok: bool, detail: str, extra: dict = None) -> None:
    """AI 动作语义审计：落 OperationLog(module=AI:action, auth_type=ai)。"""
    from system.models import OperationLog

    try:
        OperationLog.objects.create(
            module="AI:action",
            object_pk=str(getattr(user, "pk", "")),
            auth_type=OperationLog.AuthType.AI,
            status_code=1000 if ok else 1001,
            response_code=1000 if ok else 1001,
            changes=json.dumps(
                {
                    "action": action_key,
                    "params": params,
                    "status": "ok" if ok else "failed",
                    "detail": detail,
                    **(extra or {}),
                },
                ensure_ascii=False,
                default=str,
            )[:4096],
        )
    except Exception:  # noqa: BLE001 审计失败不影响业务
        logger.warning("write AI action audit failed", exc_info=True)


def execute_action(user, action_key: str, params) -> dict:
    """执行动作（调用方已完成门禁/审批）：返回 (ok, detail, data) 语义的 dict。"""
    spec = get_action(action_key)
    if spec is None:
        return {"ok": False, "detail": str(_("Unknown action")), "data": {}}
    clean, error = spec.validate(user, params if isinstance(params, dict) else {})
    if error:
        return {"ok": False, "detail": error, "data": {}}
    return spec.execute(user, clean)


def audit_ai_ask(user_obj, question: str, ok: bool, detail: str = "", usage: dict = None) -> None:
    """文档问答语义审计：落 OperationLog(module=AI:ask, auth_type=ai)。

    与 AI:action / AI:nl_query 同一采集口径（AI 观测看板的统一数据源：用量/成功率/趋势）。
    usage：LLM 供应商返回的 token 用量（成本维度观测，缺省不写）。
    """
    from system.models import OperationLog

    try:
        OperationLog.objects.create(
            module="AI:ask",
            object_pk=str(getattr(user_obj, "pk", "")),
            auth_type=OperationLog.AuthType.AI,
            status_code=1000 if ok else 1001,
            response_code=1000 if ok else 1001,
            changes=json.dumps(
                {
                    "question": (question or "")[:200],
                    "status": "ok" if ok else "failed",
                    "detail": (detail or "")[:200],
                    **({"usage": usage} if usage else {}),
                },
                ensure_ascii=False,
            )[:4096],
        )
    except Exception:  # noqa: BLE001 审计失败不影响业务
        logger.warning("write AI ask audit failed", exc_info=True)
