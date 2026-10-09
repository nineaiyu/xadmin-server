#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动态表单联动规则：校验、规范化与提交期求值（自 dform 拆分，行为不变）。

联动规则（``schema.linkages``，可空）：每条 ``{target, field, op, value, effect}``
声明「触发字段满足条件时对目标字段的效果」（隐藏/显示/必填/非必填）；服务端在提交
校验时按同一口径求值（前端镜像同一份规则做展示层联动）：

- **隐藏**的目标字段跳过必填与取值校验，且不写入落库数据（隐藏即不生效）；
- **必填/非必填**在字段自身 required 之上覆盖；
- 同一目标多条规则命中时，按数组顺序**后者覆盖前者**（分效果维度独立）。
"""

from typing import Any

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from dataset.utils.dform_constants import LINKAGE_EFFECTS, LINKAGE_OPS, MAX_LINKAGES, VALUED_LINKAGE_OPS


def _linkage_scalar(value: Any) -> str:
    """联动比较用标量字符串化（布尔 → true/false；整数化浮点去尾零；空值 → 空串）。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _linkage_value(value: Any) -> Any:
    """规则值规范化：仅接受字符串 / 数字 / 布尔（复杂结构拒绝，避免歧义比较）。"""
    if isinstance(value, (bool, int, float, str)):
        return value
    raise ValidationError(_("Invalid linkage value"))


def validate_linkages(schema: dict[str, Any], fields: list[Any]) -> list[Any]:
    """校验并规范化联动规则，返回规则列表（未声明 = 空列表）。

    - ``target`` / ``field`` 必须命中本表单字段 key，且不得自引用（语义歧义）；
    - ``op`` 值型操作符必须提供 ``value``（``in``/``notin`` 为标量列表）；
    - ``effect`` 白名单；未声明键统一丢弃（落库结构只含已声明属性）。
    """
    raw = schema.get("linkages")
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValidationError(_("Invalid form linkages"))
    if len(raw) > MAX_LINKAGES:
        raise ValidationError(_("Form linkages exceed the limit of {}").format(MAX_LINKAGES))

    keys = {item["key"] for item in fields}
    rules = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValidationError(_("Invalid form linkage rule"))
        target = str(item.get("target") or "")
        trigger = str(item.get("field") or "")
        if target not in keys:
            raise ValidationError(_("Linkage target {} is not a field of this form").format(target or "-"))
        if trigger not in keys:
            raise ValidationError(_("Linkage trigger {} is not a field of this form").format(trigger or "-"))
        if target == trigger:
            raise ValidationError(_("A linkage rule cannot target its own trigger field"))
        op = item.get("op")
        if op not in LINKAGE_OPS:
            raise ValidationError(_("Unknown linkage operator: {}").format(op))
        effect = item.get("effect")
        if effect not in LINKAGE_EFFECTS:
            raise ValidationError(_("Unknown linkage effect: {}").format(effect))
        rule = {"target": target, "field": trigger, "op": op, "effect": effect}
        value = item.get("value")
        if op in VALUED_LINKAGE_OPS:
            if op in ("in", "notin"):
                if not isinstance(value, list) or not value:
                    raise ValidationError(_("A linkage value list is required for operator {}").format(op))
                rule["value"] = [_linkage_value(entry) for entry in value]
            else:
                rule["value"] = _linkage_value(value)
        rules.append(rule)
    return rules


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _linkage_matches(rule: dict[str, Any], value: Any) -> bool:
    """触发条件求值：多值字段（checkbox/多选）按「与规则值集合有交集」判定。"""
    op = rule.get("op")
    if op == "empty":
        return _is_empty(value)
    if op == "notempty":
        return not _is_empty(value)
    expected = rule.get("value")
    candidates = expected if isinstance(expected, list) else [expected]
    expected_set = {_linkage_scalar(entry) for entry in candidates}
    if isinstance(value, list):
        matched = any(_linkage_scalar(entry) in expected_set for entry in value)
    else:
        matched = _linkage_scalar(value) in expected_set
    if op in ("eq", "in"):
        return matched
    if op in ("ne", "notin"):
        return not matched
    return False


def evaluate_linkages(schema: dict[str, Any], data: Any) -> dict[str, Any]:
    """按提交数据求值联动规则：返回 ``{字段 key: {"hidden": bool, "required": bool|None}}``。

    ``required`` 为 ``None`` 表示沿用字段自身定义；``True/False`` 为规则覆盖。
    同一目标多条命中时按数组顺序后者覆盖前者（隐藏与必填两个维度独立覆盖）。
    """
    fields = schema.get("fields") if isinstance(schema, dict) else None
    state: dict[str, dict[str, Any]] = {}
    if not isinstance(fields, list):
        return state
    for item in fields:
        if isinstance(item, dict) and item.get("key"):
            state[item["key"]] = {"hidden": False, "required": None}
    rows = data if isinstance(data, dict) else {}
    for rule in schema.get("linkages") or []:
        if not isinstance(rule, dict):
            continue
        target, trigger = rule.get("target"), rule.get("field")
        if target not in state or trigger not in state:
            continue
        if not _linkage_matches(rule, rows.get(trigger)):
            continue
        effect = rule.get("effect")
        if effect == "hide":
            state[target]["hidden"] = True
        elif effect == "show":
            state[target]["hidden"] = False
        elif effect == "require":
            state[target]["required"] = True
        elif effect == "optional":
            state[target]["required"] = False
    return state
