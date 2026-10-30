#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""权限可视化：数据权限实时试算。"""

import re
import time
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.exceptions import EmptyResultSet
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from common.core.data_scope import ScopeResult, compile_grant, validate_rules
from common.core.data_scope.menu_scope import (
    MenuScope,
    binding_permission_pks,
    menu_binding_matches_context,
    resolve_menu_scope,
    scope_mode,
    select_compiled_grants,
)
from common.core.filter import get_filter_queryset
from common.utils import get_logger
from identity.services import DeptInfo, UserInfo
from system.models import DataPermission, Menu, ModelLabelField
from system.services import ModeTypeAbstract

from .constants import TRIAL_SAMPLE_LIMIT
from .queries import _visible_menu_or_error

logger = get_logger(__name__)


def _normalize_pk_list(raw: Any) -> list[Any]:
    """兼容 str / 单值 / 列表的 pk 归一（草稿绑定菜单与上下文判定共用）。"""
    if raw in (None, ""):
        return []
    items = raw if isinstance(raw, (list, tuple, set)) else [raw]
    return [str(item) for item in items if item not in (None, "")]


def _build_draft_grant(draft: Any, menu_ctx: Any) -> Any:
    """试算草稿 → 未落库的 DataPermission 实例；不适用当前菜单上下文时返回 None。

    草稿只用于本次试算（不落库），但仍走写入侧同一套 validate_rules，
    保证「试算能过的规则保存也能过」，不成为绕过校验的后门。
    draft.menu 支持单个 pk 或 pk 列表（表单可多选绑定菜单）：与运行时菜单上下文
    （显式 action 覆盖 + 同资源 list 缺省回退；页面上下文为其下 list 权限点）有交集即参与。
    """
    if not draft or not isinstance(draft, dict):
        return None
    rules = draft.get("rules")
    if not rules:
        return None
    validate_rules(rules)
    raw_mode = draft.get("mode_type")
    try:
        mode = int(raw_mode) if raw_mode is not None else ModeTypeAbstract.ModeChoices.OR
    except (TypeError, ValueError):
        raise ValidationError("试算草稿的模式不合法") from None
    if mode not in (ModeTypeAbstract.ModeChoices.OR, ModeTypeAbstract.ModeChoices.AND):
        raise ValidationError("试算草稿的模式不合法")
    # 试算是「只读诊断」而非授权放大器：草稿含「全部数据」/通配字段规则直接 400
    # （ALLOW_ALL 草稿经 OR 合并会变成任意模型全量采样）
    from common.core.data_scope.constants import KeyChoices

    for rule in rules:
        if isinstance(rule, dict) and (rule.get("type") == KeyChoices.ALL or rule.get("field") == "*"):
            raise ValidationError("试算草稿不允许「全部数据/通配」规则")
    draft_menus = _normalize_pk_list(draft.get("menu"))
    if draft_menus and not any(menu_binding_matches_context(pk, menu_ctx) for pk in draft_menus):
        # 草稿绑定了菜单：仅在与当前上下文有交集时参与试算，否则与运行期语义不符
        return None
    menu_ids: set[str] = set()
    for pk in draft_menus:
        # 页面/目录按保存时的展开口径归一为接口权限点
        menu_ids.update(binding_permission_pks(pk))
    grant = DataPermission(name="__draft__", rules=rules, mode_type=mode, is_active=True)
    grant.menu_ids = frozenset(menu_ids)
    return grant


def _attach_menu_ids(dp: Any) -> Any:
    dp.menu_ids = frozenset(str(menu.pk) for menu in dp.menu.all())
    return dp


def _grants_for_user(user_obj: UserInfo, scope: MenuScope | None) -> list[Any]:
    """按 get_filter_queryset 的口径收集 (授权组, 来源, 部门名) 三元组。

    与运行时同一数据源与过滤条件（启用授权 / 菜单上下文 / 启用部门祖先链），
    仅用于试算诊断展示；实际过滤仍由 get_filter_queryset 独立完成。
    """
    candidates = scope.candidate_pks if scope else frozenset()
    if candidates:
        dq = Q(menu__isnull=True) | Q(menu__isnull=False, menu__pk__in=candidates)
    else:
        dq = Q(menu__isnull=True)
    rows: list[tuple[Any, ...]] = []
    dept = user_obj.dept
    if dept and dept.pk:
        chain = [str(pk) for pk in DeptInfo.recursion_dept_info(dept.pk, is_parent=True)]
        active_chain = [
            str(pk) for pk in DeptInfo.objects.filter(pk__in=chain, is_active=True).values_list("pk", flat=True)
        ]
        if active_chain:
            queryset = (
                DataPermission.objects.filter(is_active=True)
                .filter(deptinfo__in=active_chain)
                .filter(dq)
                .distinct()
                .prefetch_related("menu")
            )
            rows.extend((_attach_menu_ids(dp), "dept", dept.name) for dp in queryset)
    rows.extend(
        (_attach_menu_ids(dp), "personal", None)
        for dp in DataPermission.objects.filter(is_active=True)
        .filter(userinfo=user_obj)
        .filter(dq)
        .prefetch_related("menu")
    )
    return rows


def _source_grant_diagnosis(
    user_obj: UserInfo, model: Any, scope: MenuScope | None, draft_grant: Any
) -> tuple[list[Any], str]:
    """试算诊断：列出授权组与判定结果（解释 count 从何而来）。

    kind：all=「全部数据」放行 / condition=条件过滤 / deny=恒假 / none=与当前模型无关（不参与）。
    selected：本次是否按「显式覆盖 + list 回退」进入合并集合。
    历史脏规则编译失败时按 none 展示，不让诊断信息使试算接口失败。
    """
    rows = _grants_for_user(user_obj, scope)
    if draft_grant is not None:
        rows.append((draft_grant, "draft", None))
    compiled_rows = []
    for dp, source, dept_name in rows:
        try:
            compiled = compile_grant(dp, model, user_obj)
        except Exception as exc:  # noqa: BLE001 诊断项失败不应让只读试算 500
            logger.warning("diagnose grant failed. name:%s error:%s", dp.name, exc)
            compiled = None
        compiled_rows.append((dp, source, dept_name, compiled))
    compiled_pairs = [(dp, compiled) for dp, _, _, compiled in compiled_rows]
    selected_ids = {id(dp) for dp, _ in select_compiled_grants(compiled_pairs, scope)}

    diagnosis = []
    for dp, source, dept_name, compiled in compiled_rows:
        if compiled is None:
            kind = "none"
        elif compiled.kind == ScopeResult.KIND_ALLOW:
            kind = "all"
        elif compiled.kind == ScopeResult.KIND_DENY:
            kind = "deny"
        else:
            kind = "condition"
        diagnosis.append(
            {
                "source": source,
                "name": dp.name,
                "dept_name": dept_name,
                "applied": compiled is not None,
                "selected": id(dp) in selected_ids,
                "kind": kind,
            }
        )
    return diagnosis, scope_mode(compiled_pairs, scope)


def run_data_trial(user_obj: UserInfo, model_label: Any, menu_pk: Any, draft: Any = None) -> dict[str, Any]:
    """以目标用户为主语试算数据权限过滤（只读，不落库）。

    安全设计：
    1. 模型白名单 = 数据权限注册表（防任意表扫描 / 防非法 label 注入 apps.get_model）；
    2. 菜单上下文必须属于目标用户可见页面菜单（防任意构造上下文）；
    3. 只做 count 与 SQL 文本展示，SQL 不执行；count 为单条 SELECT COUNT(*)；
    4. 样本行只回 pk + 模型标识（str(obj)），不展开字段内容（字段权限不参与试算）；
    5. draft（可选）为「未保存的规则草稿」，用于配置页即时验证影响面，同样经写入校验。
    """
    if not model_label:
        raise ValidationError("试算模型不能为空")
    allowed = set(
        ModelLabelField.objects.filter(field_type=ModelLabelField.FieldChoices.DATA, parent__isnull=True)
        .exclude(name="*")
        .values_list("name", flat=True)
    )
    if model_label not in allowed or not re.fullmatch(r"[a-z_]+\.[a-z_]+", model_label):
        raise ValidationError("不支持的试算模型")
    app_label, model_name = model_label.split(".", 1)
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        raise ValidationError("不支持的试算模型") from None

    menu_ctx = None
    if menu_pk:
        # 上下文必须是目标用户可见的页面菜单（非法/越界统一 400，不抛 500）
        _visible_menu_or_error(user_obj, menu_pk, menu_type=Menu.MenuChoices.MENU)
        menu_ctx = str(menu_pk)

    started = time.perf_counter()
    scope = resolve_menu_scope(menu_ctx)
    draft_grant = _build_draft_grant(draft, menu_ctx)
    # 与 IsAuthenticated 的 request.user.menu 注入同构（UserInfo 无 menu 字段，纯属性）
    user_obj.menu = menu_ctx
    queryset = get_filter_queryset(model.objects.all(), user_obj)
    if draft_grant is not None:
        # 草稿只收窄不放大（草稿只收窄）：草稿效果与现有授权取交集，
        # 而非经 extra_grants 的 OR 合并——否则「全部数据」草稿等于全表采样
        from common.core.data_scope.compiler import compile_grant
        from common.core.data_scope.values import ScopeResult

        draft_effect = compile_grant(draft_grant, model, user_obj)
        if draft_effect.kind == ScopeResult.KIND_DENY:
            queryset = queryset.none()
        elif draft_effect.kind == ScopeResult.KIND_COND:
            queryset = queryset.filter(draft_effect.q)
    note = None
    if user_obj.is_superuser:
        note = "目标用户为超级管理员，试算返回全量"
    elif not settings.PERMISSION_DATA_ENABLED:
        note = "数据权限未启用，试算结果为全量"
    try:
        sql = str(queryset.query)
    except EmptyResultSet:
        # 无任何适用授权时 queryset 被短路为空结果集，编译 SQL 会抛 EmptyResultSet；
        # 这是正常的 fail-closed 结果，不该让「试算」这个诊断工具 500
        sql = "-- 无任何适用授权：查询被短路为空结果集，不产生 SQL"
    count = queryset.count()
    sample = []
    for obj in queryset[:TRIAL_SAMPLE_LIMIT]:
        try:
            label = str(obj)
        except Exception:  # noqa: BLE001 异常 __str__ 不应让诊断工具失败
            label = ""
        sample.append({"pk": str(obj.pk), "label": label})
    diagnosis, mode = _source_grant_diagnosis(user_obj, model, scope, draft_grant)
    return {
        "scope": "data",
        "model": model_label,
        "menu": menu_ctx,
        "scope_mode": mode,
        "count": count,
        "sql": sql,
        "sample": sample,
        "sample_limit": TRIAL_SAMPLE_LIMIT,
        "grants": diagnosis,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "is_superuser": user_obj.is_superuser,
        "data_enabled": settings.PERMISSION_DATA_ENABLED,
        "draft_applied": bool(draft_grant),
        "note": note,
    }
