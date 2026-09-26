#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""影响面预检与引用保护。

两种消费形态：

1. **预览**（只读）：``impact_for(obj)`` / ``impact_for_many(objs)`` 汇总「删除/停用这个
   对象会影响谁」——按资源声明计算器（角色 → 用户数、部门 → 子部门 + 人数、字典 →
   表单引用数、数据集 → 卡片 / 报表数、流程 → 实例数、表单 → 提交数、大屏 → 引用数、
   菜单 → 子菜单 + 角色绑定数），带引用方样本与处置建议；
2. **引用保护**（fail-closed 可开关）：模型登记在 ``IMPACT_GUARD_MODELS`` 配置里且影响面
   非空时，删除必须显式带 ``impact_confirmed=true``（前端弹窗确认后自动携带），否则
   返回可读错误。默认清单为空 = 不阻断（渐进启用，零行为变化）。

计数口径：全部走索引列（FK / M2M 反向查询）；JSON 引用（仪表盘 layout / 大屏
dashboards / 表单 schema）在 Python 侧扫描（规模有界，避免跨库 JSON 查询差异）。
"""

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

from common.utils import get_logger

logger = get_logger(__name__)

#: 引用方样本条数上限（预检弹窗只展示前若干条，避免大列表）
SAMPLE_LIMIT = 5
#: 单个资源的检查项上限
MAX_ITEMS = 8

IMPACT_GUARD_CONFIG_KEY = "IMPACT_GUARD_MODELS"


@dataclass(frozen=True)
class ImpactCheck:
    """单条影响面检查项：计数 + 引用方样本 + 处置建议。"""

    key: str
    label: str
    count: int
    hint: str = ""
    samples: tuple = ()


def _check(key: str, label, count: int, hint="", samples=None) -> ImpactCheck:
    return ImpactCheck(
        key=key,
        label=str(label),
        count=int(count or 0),
        hint=str(hint) if hint else "",
        samples=tuple(samples or ()),
    )


def _impact_userrole(obj) -> list:
    from system.models import UserInfo

    users = UserInfo.objects.filter(roles=obj, is_active=True)
    return [
        _check(
            "role_users",
            _("Users bound to this role"),
            users.count(),
            hint=_("Reassign these users to another role before deleting"),
            samples=users.values_list("username", flat=True)[:SAMPLE_LIMIT],
        ),
        _check(
            "role_menus",
            _("Menus authorized to this role"),
            obj.menu.count(),
            hint=_("Menu authorizations will be removed together with the role"),
            samples=obj.menu.values_list("name", flat=True)[:SAMPLE_LIMIT],
        ),
    ]


def _impact_deptinfo(obj) -> list:
    from system.models import DeptInfo, UserInfo

    children = DeptInfo.objects.filter(parent=obj)
    users = UserInfo.objects.filter(dept=obj)
    return [
        _check(
            "dept_children",
            _("Sub departments"),
            children.count(),
            hint=_("Sub departments will be detached or blocked by the parent relation"),
            samples=children.values_list("name", flat=True)[:SAMPLE_LIMIT],
        ),
        _check(
            "dept_users",
            _("Members of this department"),
            users.count(),
            hint=_("Members keep their accounts but lose this department"),
            samples=users.values_list("username", flat=True)[:SAMPLE_LIMIT],
        ),
    ]


def _impact_datadict(obj) -> list:
    from dataset.models import DynamicForm

    children = obj.children.count() if getattr(obj, "parent_id", None) is None else 0
    forms = []
    for form in DynamicForm.objects.filter(is_active=True).only("name", "schema").iterator():
        fields = (form.schema or {}).get("fields") if isinstance(form.schema, dict) else form.schema
        for field in fields or []:
            if isinstance(field, dict) and str(field.get("dict") or "") == str(obj.code):
                forms.append(form.name)
                break
    return [
        _check("dict_children", _("Child dictionary items"), children, hint=_("Child items will be removed")),
        _check(
            "dict_form_refs",
            _("Dynamic form fields referencing this dictionary"),
            len(forms),
            hint=_("Change these form fields to another dictionary before deleting"),
            samples=forms[:SAMPLE_LIMIT],
        ),
    ]


def _impact_dataset(obj) -> list:
    from dataset.models import Dashboard, Report, Screen

    dashboards = []
    for dashboard in Dashboard.objects.only("name", "layout").iterator():
        for card in dashboard.layout or []:
            if isinstance(card, dict) and str(card.get("dataset") or "") == str(obj.pk):
                dashboards.append(dashboard.name)
                break
    screens = [
        screen.name
        for screen in Screen.objects.only("name", "dashboards").iterator()
        if str(obj.pk) in [str(item) for item in (screen.dashboards or [])]
    ]
    reports = Report.objects.filter(dataset=obj)
    return [
        _check(
            "dataset_cards",
            _("Dashboard cards using this dataset"),
            len(dashboards),
            hint=_("Remove or repoint these cards before deleting"),
            samples=dashboards[:SAMPLE_LIMIT],
        ),
        _check(
            "dataset_screens",
            _("Screens using this dataset"),
            len(screens),
            hint=_("Remove the dataset from these screens before deleting"),
            samples=screens[:SAMPLE_LIMIT],
        ),
        _check(
            "dataset_reports",
            _("Scheduled reports bound to this dataset"),
            reports.count(),
            hint=_("Delete or rebind these reports first (database protected)"),
            samples=reports.values_list("name", flat=True)[:SAMPLE_LIMIT],
        ),
    ]


def _impact_approvalflow(obj) -> list:
    return [
        _check(
            "flow_instances",
            _("Approval instances"),
            obj.instances.count(),
            hint=_("The flow is referenced by approval instances and cannot be deleted"),
            samples=obj.instances.values_list("title", flat=True)[:SAMPLE_LIMIT],
        ),
        _check("flow_nodes", _("Flow nodes"), obj.nodes.count(), hint=_("Nodes will be removed together")),
        _check("flow_versions", _("Version snapshots"), obj.versions.count()),
    ]


def _impact_dynamicform(obj) -> list:
    return [
        _check(
            "form_submissions",
            _("Form submissions"),
            obj.submissions.count(),
            hint=_("Submissions reference this form; deleting removes the schema linkage"),
            samples=obj.submissions.values_list("pk", flat=True)[:SAMPLE_LIMIT],
        ),
    ]


def _impact_screen(obj) -> list:
    return [
        _check(
            "screen_dashboards",
            _("Dashboards shown on this screen"),
            len(obj.dashboards or []),
            hint=_("Only the screen layout is removed; dashboards keep untouched"),
        ),
    ]


def _impact_menu(obj) -> list:
    from system.models import Menu, UserRole

    children = Menu.objects.filter(parent=obj)
    roles = UserRole.objects.filter(menu=obj)
    return [
        _check(
            "menu_children",
            _("Child menus"),
            children.count(),
            hint=_("Child menus will lose their parent and may become unreachable"),
            samples=children.values_list("name", flat=True)[:SAMPLE_LIMIT],
        ),
        _check(
            "menu_roles",
            _("Roles authorized with this menu"),
            roles.count(),
            hint=_("Role authorizations will be cleaned up"),
            samples=roles.values_list("name", flat=True)[:SAMPLE_LIMIT],
        ),
    ]


#: 影响面计算器注册表：模型 label_lower → 计算器（返回 ImpactCheck 列表）
IMPACT_CALCULATORS = {
    "system.userrole": _impact_userrole,
    "system.deptinfo": _impact_deptinfo,
    "system.datadict": _impact_datadict,
    "dataset.dataset": _impact_dataset,
    "approval.approvalflow": _impact_approvalflow,
    "dataset.dynamicform": _impact_dynamicform,
    "dataset.screen": _impact_screen,
    "system.menu": _impact_menu,
}


def supports_impact(model_label: str) -> bool:
    return str(model_label or "") in IMPACT_CALCULATORS


def impact_for(obj) -> dict:
    """单对象影响面（无计算器 = 零影响，前端不展示弹窗）。"""
    model_label = type(obj)._meta.label_lower
    calculator = IMPACT_CALCULATORS.get(model_label)
    checks: list = []
    if calculator is not None:
        try:
            checks = [item for item in (calculator(obj) or []) if item.count][:MAX_ITEMS]
        except Exception:  # noqa: BLE001 单个计算器失败不影响其余检查
            logger.warning("impact calculation failed. model:%s pk:%s", model_label, obj.pk, exc_info=True)
            checks = []
    suggestions = []
    for item in checks:
        if item.hint and item.hint not in suggestions:
            suggestions.append(item.hint)
    return {
        "pk": str(obj.pk),
        "model": model_label,
        "name": str(obj)[:120],
        "has_impact": bool(checks),
        "items": [
            {
                "key": item.key,
                "label": item.label,
                "count": item.count,
                "hint": item.hint,
                "samples": list(item.samples),
            }
            for item in checks
        ],
        "suggestions": suggestions,
    }


def impact_for_many(objects) -> dict:
    """批量影响面汇总（前端删除/批量删除前的预检载荷）。"""
    results = [impact_for(obj) for obj in objects]
    totals: dict = {}
    for result in results:
        for item in result["items"]:
            entry = totals.setdefault(item["key"], {"key": item["key"], "label": item["label"], "count": 0})
            entry["count"] += item["count"]
    return {
        "results": results,
        "totals": [totals[key] for key in sorted(totals)],
        "has_impact": any(result["has_impact"] for result in results),
    }


def guarded_models() -> set:
    """引用保护开关：``IMPACT_GUARD_MODELS``（模型 label_lower 清单，默认空 = 不阻断）。"""
    from django.conf import settings

    values = getattr(settings, IMPACT_GUARD_CONFIG_KEY, []) or []
    return {str(item).strip().lower() for item in values if str(item).strip()}


def _is_confirmed(request) -> bool:
    truthy = ("1", "true", "yes", "on")
    data = getattr(request, "data", None)
    if isinstance(data, dict) and str(data.get("impact_confirmed", "")).strip().lower() in truthy:
        return True
    params = getattr(request, "query_params", None)
    if params is not None:
        return str(params.get("impact_confirmed", "")).strip().lower() in truthy
    return False


def ensure_impact_confirmed(view, request, instances=None, queryset=None) -> None:
    """删除前引用保护：登记在 IMPACT_GUARD_MODELS 的模型有影响面时要求显式确认。

    - 未登记模型 / 已带 ``impact_confirmed=true`` / 影响面为空 → 直接放行（未登记
      模型零额外查询：先判模型再决定是否展开 queryset）；
    - 命中 → 抛可读 ValidationError（前端弹窗确认后重发，不做静默阻断）。
    """
    model = None
    if instances:
        model = type(instances[0])
    elif queryset is not None:
        model = getattr(queryset, "model", None)
    if model is None:
        model = getattr(getattr(view, "queryset", None), "model", None)
    if model is None or model._meta.label_lower not in guarded_models():
        return
    if _is_confirmed(request):
        return
    if instances is None:
        instances = list(queryset or [])
    summary = impact_for_many(instances)
    if not summary["has_impact"]:
        return
    total = sum(item["count"] for item in summary["totals"])
    raise ValidationError(
        str(
            _(
                "This operation affects {count} related records. Confirm the impact preview before deleting "
                "(add impact_confirmed=true to proceed)"
            )
        ).format(count=total)
    )
