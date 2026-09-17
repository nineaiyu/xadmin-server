#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""权限可视化：数据权限规则的解码（JSON → 可读文案）。"""

import copy
import json

from common.utils import get_logger
from system.models import DataPermission, DeptInfo, Menu, UserInfo, UserRole
from system.services import ModeTypeAbstract
from system.utils.rule_meta import MATCH_TEXTS, MODE_AND_TEXT, MODE_OR_TEXT, RULE_TYPE_TEXTS

from .constants import PREVIEW_VALUE_NAME_LIMIT
from .labels import _field_label, _humanize_seconds, _join_names, _model_label, _pks_of

logger = get_logger(__name__)


def _resolve_value_text(rule: dict, user_obj: UserInfo | None, subject: str = "user") -> str:
    """按规则类型把 value 解析为可读名称（关联 ID → 用户名/角色名/部门名/菜单标题）。

    注意：value.user.* 类型的原始 value 就是 '*' 占位符（过滤时注入真实 ID），
    必须先按类型解析，仅对自由值类型把 '*' 视为不限。
    历史脏数据（非法 JSON / 非预期结构）不应让预览接口 500，统一回退原始值文案。

    subject="dept" 时主语是「部门成员」（部门维度预览没有单一用户上下文），
    注入类类型直接给固定文案、不访问 user_obj。
    """
    try:
        return _resolve_value_text_inner(rule, user_obj, subject)
    except Exception as exc:  # noqa: BLE001 预览是只读展示，任何脏数据都不应 500
        logger.warning("decode rule value failed, fallback to raw. type:%s error:%s", rule.get("type"), exc)
        return str(rule.get("value"))


def _resolve_dept_subject_text(f_type, val) -> str:
    """部门维度：注入类类型的主语是「部门成员」，与具体用户无关。"""
    mapping = {
        "value.user.id": "部门成员本人（各自）",
        "value.user.dept.id": "部门成员所在部门",
        "value.user.dept.ids": "部门成员所在部门及下级",
        "value.leader.dept.ids": "部门成员主管的部门及下级",
        "value.leader.user.ids": "部门成员主管部门的成员",
    }
    if f_type in mapping:
        return mapping[f_type]
    # 与具体用户无关的类型（指定部门/指定用户/时间窗等）复用通用解析
    return _resolve_value_text_inner({"type": f_type, "value": val}, None, "user")


def _resolve_value_text_inner(rule: dict, user_obj: UserInfo | None, subject: str = "user") -> str:
    f_type, val = rule.get("type"), rule.get("value")
    if f_type == "value.all":
        return "不限（全部）"
    if subject == "dept":
        return _resolve_dept_subject_text(f_type, val)
    if f_type == "value.user.id":
        return f"目标用户本人（{user_obj.username}）"
    if f_type == "value.user.dept.id":
        dept = user_obj.dept
        return f"目标用户所在部门（{dept.name}）" if dept else "目标用户未绑定部门"
    if f_type == "value.user.dept.ids":
        if not user_obj.dept:
            return "目标用户未绑定部门"
        pks = [str(pk) for pk in DeptInfo.recursion_dept_info(user_obj.dept.pk)]
        return _join_names(DeptInfo.objects.filter(pk__in=pks).values_list("name", flat=True))
    if f_type == "value.dept.ids":
        names = []
        for pk in _pks_of(val):
            pks = [str(item) for item in DeptInfo.recursion_dept_info(str(pk))]
            names.extend(DeptInfo.objects.filter(pk__in=pks).values_list("name", flat=True))
        return _join_names(dict.fromkeys(names))
    if f_type == "value.leader.dept.ids":
        led = getattr(user_obj, "leader_depts", None)
        if led is None or not led.exists():
            return "目标用户不是任何部门主管"
        names = []
        for dept in led.filter(is_active=True):
            pks = [str(pk) for pk in DeptInfo.recursion_dept_info(dept.pk)]
            names.extend(DeptInfo.objects.filter(pk__in=pks).values_list("name", flat=True))
        return _join_names(dict.fromkeys(names))
    if f_type == "value.leader.user.ids":
        led = getattr(user_obj, "leader_depts", None)
        if led is None or not led.exists():
            return "目标用户不是任何部门主管"
        dept_pks = []
        for dept in led.filter(is_active=True):
            dept_pks.extend(str(pk) for pk in DeptInfo.recursion_dept_info(dept.pk))
        queryset = UserInfo.objects.filter(dept__in=dept_pks)
        rows = list(queryset.values_list("nickname", "username")[:PREVIEW_VALUE_NAME_LIMIT])
        return _join_names([nickname or username for nickname, username in rows], total=queryset.count())
    if f_type == "value.table.user.ids":
        queryset = UserInfo.objects.filter(pk__in=_pks_of(val))
        rows = list(queryset.values_list("nickname", "username")[:PREVIEW_VALUE_NAME_LIMIT])
        return _join_names([nickname or username for nickname, username in rows], total=queryset.count())
    if f_type == "value.table.menu.ids":
        queryset = Menu.objects.filter(pk__in=_pks_of(val))
        titles = queryset.values_list("meta__title", flat=True)[:PREVIEW_VALUE_NAME_LIMIT]
        return _join_names(titles, total=queryset.count())
    if f_type == "value.table.role.ids":
        queryset = UserRole.objects.filter(pk__in=_pks_of(val))
        names = queryset.values_list("name", flat=True)[:PREVIEW_VALUE_NAME_LIMIT]
        return _join_names(names, total=queryset.count())
    if f_type == "value.table.dept.ids":
        queryset = DeptInfo.objects.filter(pk__in=_pks_of(val))
        names = queryset.values_list("name", flat=True)[:PREVIEW_VALUE_NAME_LIMIT]
        return _join_names(names, total=queryset.count())
    if f_type == "value.date":
        seconds = json.loads(val) if isinstance(val, str) else val
        direction = "过去" if int(seconds) < 0 else "未来"
        return f"{direction} {_humanize_seconds(seconds)}内"
    if f_type == "value.datetime.range":
        return "{} ~ {}".format(*val) if isinstance(val, list) and len(val) == 2 else str(val)
    if val == "*":
        return "不限（全部）"
    return str(val)


def decode_rule(rule: dict, user_obj: UserInfo | None, label_cache: dict | None = None, subject: str = "user") -> dict:
    """单条规则 JSON → 可读文案结构（table/field/type/match/value 全部附 label）。"""
    table = rule.get("table")
    field = rule.get("field")
    return {
        "table": table,
        "table_label": "全部表" if table == "*" else _model_label(table, label_cache),
        "field": field,
        "field_label": "全部字段" if field == "*" else _field_label(table, field, label_cache),
        "type": rule.get("type"),
        "type_text": RULE_TYPE_TEXTS.get(rule.get("type"), str(rule.get("type"))),
        "match": rule.get("match", "exact"),
        "match_text": MATCH_TEXTS.get(rule.get("match", "exact"), str(rule.get("match", "exact"))),
        "value": rule.get("value"),
        "value_text": _resolve_value_text(copy.deepcopy(rule), user_obj, subject),
        "exclude": bool(rule.get("exclude")),
    }


def decode_data_permission(
    dp: DataPermission, user_obj: UserInfo | None, label_cache: dict | None = None, subject: str = "user"
) -> dict:
    """DataPermission → 可读授权组（含生效模式与总述文案）。

    与 common/core/data_scope.py 组内语义对齐：value.all 在或模式下短路全放行、
    在且模式下该规则被忽略（代数下即 ALLOW 单位元）。deepcopy 防止解码过程改写 JSONField 内存值。

    menu_scoped=True 表示该授权绑定了菜单，只在对应菜单上下文生效（通用列表接口下不生效），
    展示层据此提示，避免「预览显示有授权、实际看不到」的误读。
    """
    rules = [rule for rule in copy.deepcopy(dp.rules) if isinstance(rule, dict)]
    effective_mode = ModeTypeAbstract.ModeChoices.OR if len(rules) == 1 else dp.mode_type
    decoded = []
    for rule in rules:
        item = decode_rule(rule, user_obj, label_cache, subject)
        if rule.get("type") == "value.all" and effective_mode == ModeTypeAbstract.ModeChoices.AND:
            # 且模式存在 value.all：该规则被忽略（代数下 ALLOW 是单位元）
            item["value_text"] = "且模式下被忽略"
        decoded.append(item)
    rule_text = "; ".join(
        f"【{item['table_label']}】{item['field_label']} {item['match_text']} {item['value_text']}" for item in decoded
    )
    mode_text = MODE_OR_TEXT if effective_mode == ModeTypeAbstract.ModeChoices.OR else MODE_AND_TEXT
    menus = [{"pk": str(menu.pk), "title": menu.meta.title} for menu in dp.menu.all()]
    return {
        "pk": str(dp.pk),
        "name": dp.name,
        "is_active": dp.is_active,
        "mode_type": dp.mode_type,
        "menus": menus,
        "menu_scoped": bool(menus),
        "rules": decoded,
        "rule_text": f"{mode_text}: {rule_text}",
    }
