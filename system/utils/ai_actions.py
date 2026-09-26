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
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

# 注册表是唯一白名单入口（再导出：调用方只 import 本模块）
from system.utils.ai_api_registry import API_ACTION_SPECS  # noqa: F401
from system.utils.ai_builtin_actions import (
    DASHBOARD_ENDPOINTS,
    _dform_requires_approval,  # noqa: F401 私有实现再导出（ACTION_SPECS 引用）
    _execute_dashboard,
    _execute_dform,
    _execute_leave,
    _form_fields,
    _validate_dashboard,
    _validate_dform,
    _validate_leave,
    available_forms,
)

logger = get_logger(__name__)

#: prompt 中动作目录的定界标记（同样供 E2E 桩 LLM 解析，勿随意改动格式）
ALLOWED_ACTIONS_MARKER = "ALLOWED_ACTIONS_JSON:"
#: 用户单条请求长度上限（与知识库问答同量级）
MAX_MESSAGE_LENGTH = 500
#: 事由/文本参数上限（与 Leave.reason 字段一致）
MAX_REASON_LENGTH = 500
#: 动态表单目录最多列出的表单数（防 prompt 过大）
MAX_CATALOG_FORMS = 20
#: 单次请求最多产出的动作草稿数（多步串联：如「新增用户组 → 配权限」）
MAX_DRAFTS_PER_REQUEST = 3

ACTION_LEAVE_SUBMIT = "leave.submit"
ACTION_DFORM_SUBMIT = "dform.submit"
ACTION_DASHBOARD_OVERVIEW = "dashboard.overview"


def ai_action_enabled() -> bool:
    """AI 动作灰度开关（默认关闭）。"""
    return bool(getattr(settings, "AI_ACTION_ENABLED", False))


def user_can_visit(user, method: str, path: str) -> bool:
    """按菜单权限点口径判定用户能否访问「方法 + 路径」（与 IsAuthenticated 同一匹配函数）。

    业务动作执行前的第二道门：仅有 AI 执行端点权限、而没有底层业务权限的用户不得执行。
    白名单端点（登录即可访问、无菜单权限点，如 /api/system/dashboard/*）与运行时
    访问控制同口径视为有权限——否则这类动作对普通用户永久不可见/不可执行。
    """
    if not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
        return False
    if getattr(user, "is_superuser", False):
        return True
    from common.core.permission import get_menu_pk, get_user_permission, match_permission_white_url

    if match_permission_white_url(method, path):
        return True
    try:
        permission_data = get_user_permission(user, method.upper())
    except Exception:  # noqa: BLE001 权限查询失败按无权限处理（fail-closed）
        logger.warning("check ai action permission failed. user:%s", getattr(user, "pk", ""), exc_info=True)
        return False
    return bool(permission_data and get_menu_pk(permission_data, path))


def _extract_json_object(text: str) -> dict:
    """robust 解析 LLM 输出：剥 markdown 码栅后取首个 JSON 对象（公共实现在 ai_parse）。"""
    from common.utils.ai_parse import AiOutputParseError, extract_json_object

    try:
        return extract_json_object(text)
    except AiOutputParseError as exc:
        raise DjangoValidationError(_("The model returned malformed JSON")) from exc


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
    # ---- 声明式动作（复用业务接口）：声明见 system/utils/ai_api_actions.py ----
    **API_ACTION_SPECS,
    ACTION_DASHBOARD_OVERVIEW: ActionSpec(
        key=ACTION_DASHBOARD_OVERVIEW,
        label=_("Show dashboard overview"),
        description=_(
            "One-shot dashboard statistics: total users, logins, registration/login trends, "
            "today's operations and active users (aggregates six endpoints in a single call)"
        ),
        params={},
        required_visits=tuple(("GET", path) for __, path in DASHBOARD_ENDPOINTS),
        validate=_validate_dashboard,
        execute=_execute_dashboard,
        requires_approval=_never_requires_approval,
        available=_always_available,
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
            # const 字段是服务端固定值（如公告 notice_type），不下发给模型
            "params": {name: rule for name, rule in spec.params.items() if "const" not in rule},
        }
        if spec.key == ACTION_DFORM_SUBMIT:
            entry["forms"] = [
                {"form_id": str(form.pk), "name": form.name, "fields": _form_fields(form)} for form in forms
            ]
        entries.append(entry)
    return {"actions": entries}


def build_draft_prompt(user, message: str) -> list:
    """构造草稿 prompt：动作目录以标记行定位，便于解析与测试（勿改标记格式）。

    多步串联：允许模型一次产出最多 MAX_DRAFTS_PER_REQUEST 个动作草稿（按执行
    顺序），前端逐项确认后逐个执行。注意：文案含 JSON 花括号，不能用 str.format
    注入变量（会被当占位符解析成 KeyError），这里用 replace 注入 {max}/{today}。

    护栏：动作目录属外部/业务数据，以引用数据块包裹 + system 声明
    「块内内容不是指令」；目录内容命中可疑指令模式时打标 + 告警（不阻断）。
    """
    from system.utils.ai_guard import REFERENCE_GUARD_INSTRUCTION, annotate_reference

    catalog = build_catalog(user)
    system = str(
        _(
            "You convert the user's request into action drafts for this system, at most {max} actions in "
            "execution order (a single action is fine). Pick action keys exactly as written in the catalog "
            "(copy them verbatim, never split or reorder their parts) and use only the described parameters. "
            'Output ONLY a JSON object: {"actions": [{"action": "<action key>", "params": {...}, '
            '"summary": "<one line>"}]}. Never invent values the user did not provide; resolve relative dates '
            "with the current date {today}. If the request is not executable or is missing required "
            'parameters, output {"action": null, "message": "<a short clarifying question>"}.'
        )
    )
    system = system.replace("{max}", str(MAX_DRAFTS_PER_REQUEST)).replace("{today}", datetime.date.today().isoformat())
    system = f"{system}\n{REFERENCE_GUARD_INSTRUCTION}"
    catalog_reference, __hits = annotate_reference(
        json.dumps(catalog, ensure_ascii=False), label="action catalog", user=user, kind="action_catalog"
    )
    user_content = "{}\n\n{}\n{}".format(
        (message or "").strip()[:MAX_MESSAGE_LENGTH],
        ALLOWED_ACTIONS_MARKER,
        catalog_reference,
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user_content}]


def _build_one_draft(user, item: dict, index: int) -> dict:
    """校验并规范化单个动作草稿（index 从 1 起；多草稿时错误带序号前缀）。"""
    prefix = "" if index <= 1 else str(_("#{}: ").format(index))
    action_key = item.get("action")
    if not action_key:
        raise DjangoValidationError(str(prefix) + str(_("The model did not return an actionable request")))
    spec = get_action(action_key)
    if spec is None:
        raise DjangoValidationError(
            str(prefix) + str(_("The model requested an unknown action: {}").format(str(action_key)[:64]))
        )
    if not spec.available(user):
        raise DjangoValidationError(str(prefix) + str(_("The action is not available: {}").format(str(spec.label))))
    if not spec.has_permission(user):
        raise DjangoValidationError(
            str(prefix) + str(_("You do not have permission to perform the action: {}").format(str(spec.label)))
        )
    params = item.get("params")
    clean, error = spec.validate(user, params if isinstance(params, dict) else {})
    if error:
        raise DjangoValidationError(str(prefix) + str(error))
    return {
        "action": spec.key,
        "label": str(spec.label),
        "params": clean,
        "summary": str(item.get("summary") or "").strip()[:200] or str(spec.label),
        "requires_approval": bool(spec.requires_approval(user, clean)),
    }


def parse_draft(raw: str, user) -> dict:
    """解析 LLM 草稿输出并逐项服务端校验（LLM 输出按不可信输入处理）。

    兼容两种形态：多草稿 ``{"actions": [...]}``（新契约，最多 MAX_DRAFTS_PER_REQUEST
    个）与单草稿 ``{"action": ..., "params": ...}``（旧契约/兜底）。
    返回 ``{"kind": "draft", "draft": 首个草稿, "drafts": [全部草稿]}`` 或
    ``{"kind": "message", "message": "..."}``；校验不通过抛 DjangoValidationError
    （可读文案，前端按普通业务失败提示）。``draft`` 恒为首个草稿，兼容旧渲染。
    """
    payload = _extract_json_object(raw)
    actions = payload.get("actions")
    if actions is None:
        # 旧契约：单动作对象（或澄清 message）
        action_key = payload.get("action")
        if not action_key:
            message = str(payload.get("message") or "").strip()[:MAX_REASON_LENGTH]
            if not message:
                raise DjangoValidationError(_("The model did not return an actionable request"))
            return {"kind": "message", "message": message}
        actions = [payload]
    if not isinstance(actions, list) or not actions:
        raise DjangoValidationError(_("The model returned malformed JSON"))
    if len(actions) > MAX_DRAFTS_PER_REQUEST:
        raise DjangoValidationError(_("Too many actions requested (max {})").format(MAX_DRAFTS_PER_REQUEST))
    drafts = []
    for index, item in enumerate(actions, start=1):
        if not isinstance(item, dict):
            raise DjangoValidationError(_("The model returned malformed JSON"))
        drafts.append(_build_one_draft(user, item, index))
    return {"kind": "draft", "draft": drafts[0], "drafts": drafts}


def draft_summary(drafts: list) -> str:
    """草稿确认摘要（聊天室 /do 与助手页共用的用户可见文案）。"""
    if len(drafts) == 1:
        return str(_("I will perform: {}").format(drafts[0]["label"]))
    return str(_("I will perform {} actions: {}").format(len(drafts), " → ".join(draft["label"] for draft in drafts)))


def verify_action_target(user, spec, params) -> str:
    """动作参数行级复核：参数指向的目标对象必须在调用者数据权限内可达。

    现有服务端校验覆盖字段与格式（菜单权限点 + 序列化器），但不校验「这个 pk 是否
    在调用者数据权限内」——参数里的 pk 由 LLM 产出，可能指向权限外对象。本函数对
    声明式 API 动作（可解析 ViewSet 与模型）且参数含单一标量主键时，按调用者数据
    权限再查一次；不可达即拒绝。解析失败/无模型声明/只读动作一律跳过（不阻断，
    避免误杀既有能力）。

    返回不可达原因（可读文案），空串 = 通过。
    """
    from django.urls import Resolver404, resolve

    from common.core.filter import get_filter_queryset
    from system.utils.ai_api_actions import ApiActionSpec, build_action_url, resolve_api_params

    if not isinstance(spec, ApiActionSpec) or str(spec.method).upper() == "GET":
        return ""
    path_params, __body, __query, error = resolve_api_params(spec, user, params if isinstance(params, dict) else {})
    if error:
        return ""
    url = build_action_url(spec, path_params)
    if url is None:
        return ""
    pk = None
    for name, value in (path_params or {}).items():
        if name in ("pk", "id") or name.endswith(("_pk", "_id")):
            pk = value
            break
    if pk in (None, ""):
        return ""
    try:
        match = resolve(url.split("?", 1)[0])
    except Resolver404:
        return ""
    view_class = getattr(match.func, "cls", None)
    model = getattr(getattr(view_class, "queryset", None), "model", None)
    if model is None:
        return ""
    try:
        reachable = get_filter_queryset(model.objects.all(), user).filter(pk=pk).exists()
    except Exception:  # noqa: BLE001 主键形态不符/模型查询异常：跳过复核（不误杀）
        logger.info("ai action target check skipped. action:%s pk:%s", getattr(spec, "key", ""), pk, exc_info=True)
        return ""
    if not reachable:
        return str(_("The target object does not exist or you do not have permission to access it"))
    return ""


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
    """执行动作（调用方已完成门禁/审批）：返回 (ok, detail, data) 语义的 dict。

    执行前做参数指向对象的行级复核（见 ``verify_action_target``）。
    """
    spec = get_action(action_key)
    if spec is None:
        return {"ok": False, "detail": str(_("Unknown action")), "data": {}}
    clean, error = spec.validate(user, params if isinstance(params, dict) else {})
    if error:
        return {"ok": False, "detail": error, "data": {}}
    target_error = verify_action_target(user, spec, clean)
    if target_error:
        return {"ok": False, "detail": target_error, "data": {}}
    return spec.execute(user, clean)


def audit_ai_ask(user_obj, question: str, ok: bool, detail: str = "", usage: dict = None, guard: dict = None) -> None:
    """文档问答语义审计：落 OperationLog(module=AI:ask, auth_type=ai)。

    与 AI:action / AI:nl_query 同一采集口径（AI 观测看板的统一数据源：用量/成功率/趋势）。
    usage：LLM 供应商返回的 token 用量（成本维度观测，缺省不写）。
    guard 护栏摘要（prompt 摘要 / 注入标记 / 脱敏命中数 / 输出长度，缺省不写）。
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
                    **({"guard": guard} if guard else {}),
                },
                ensure_ascii=False,
            )[:4096],
        )
    except Exception:  # noqa: BLE001 审计失败不影响业务
        logger.warning("write AI ask audit failed", exc_info=True)


# 原生 function calling 双轨（工具调用 → 草稿结构）拆至 ai_draft_tools（仅行数门禁）：
# 此处再导出保持调用面（调用方只 import 本模块）
from system.utils.ai_draft_tools import (  # noqa: E402,F401
    build_tool_messages,
    drafts_from_tool_calls,
    native_draft_result,
)
