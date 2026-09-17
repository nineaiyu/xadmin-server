#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""数据权限编译器：规则值解析与布尔代数组合。"""

import datetime
import ipaddress
import json
import re
from functools import reduce

from django.db.models import Q
from django.forms.utils import from_current_timezone
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from system.services import DeptInfo, UserInfo

from .constants import _NUMERIC_LOOKUPS, AND_MODE, OR_MODE, TABLE_TYPES, KeyChoices


def _as_bool(value):
    """isnull 专用：bool 原样返回，``"true"/"1"/"false"/"0"`` 归一，其余返回 None（非法）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1"):
            return True
        if lowered in ("false", "0"):
            return False
    return None


def normalize_match_value(match, value):
    """严格类型 lookup 的 value 归一；返回 None 表示形态非法（调用方 fail-closed）。

    ``_NUMERIC_LOOKUPS`` 归一为 int（Django 的 year/month 等 lookup 只接受数字）；
    其余匹配符原样透传。
    """
    if match == "isnull":
        return _as_bool(value)
    if match == "range":
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return list(value)
        return None
    if match in _NUMERIC_LOOKUPS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return value


class ScopeResult:
    """数据范围运算结果：恒真 / 恒假 / 条件 Q。"""

    __slots__ = ("kind", "q")

    KIND_ALLOW = "allow_all"
    KIND_DENY = "deny_all"
    KIND_COND = "condition"

    def __init__(self, kind, q=None):
        self.kind = kind
        self.q = q

    def __repr__(self):  # pragma: no cover 调试辅助
        return f"ScopeResult({self.kind}, {self.q})"


ALLOW_ALL = ScopeResult(ScopeResult.KIND_ALLOW)
DENY_ALL = ScopeResult(ScopeResult.KIND_DENY)


def condition_result(q):
    return ScopeResult(ScopeResult.KIND_COND, q)


def combine(results, mode=OR_MODE):
    """ScopeResult 布尔代数组合。

    AND：DENY_ALL 是零元（支配）、ALLOW_ALL 是单位元（忽略）；
    OR：ALLOW_ALL 是零元（支配）、DENY_ALL 是单位元（忽略）。
    空输入按各自单位元取值（AND → ALLOW_ALL、OR → DENY_ALL），
    由调用方保证空集语义（如「组内规则被 table 过滤后为空」应整组跳过而非进入 combine）。
    """
    if not results:
        return ALLOW_ALL if mode == AND_MODE else DENY_ALL
    if mode == AND_MODE:
        if any(r.kind == ScopeResult.KIND_DENY for r in results):
            return DENY_ALL
        qs = [r.q for r in results if r.kind == ScopeResult.KIND_COND]
        if not qs:
            return ALLOW_ALL
        return condition_result(reduce(lambda a, b: a & b, qs))
    if any(r.kind == ScopeResult.KIND_ALLOW for r in results):
        return ALLOW_ALL
    qs = [r.q for r in results if r.kind == ScopeResult.KIND_COND]
    if not qs:
        return DENY_ALL
    return condition_result(reduce(lambda a, b: a | b, qs))


def _json_value(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _pk_list(value):
    items = _json_value(value)
    if not isinstance(items, (list, tuple)):
        items = [items]
    pks = []
    for item in items or []:
        if isinstance(item, dict) and "pk" in item:
            pks.append(item["pk"])
        elif item is not None:
            pks.append(item)
    return pks


def _flatten_dept_tree(raw):
    """指定部门列表 → 各自及全部下级的并集。"""
    merged = []
    for pk in _pk_list(raw):
        merged.extend(DeptInfo.recursion_dept_info(str(pk)))
    return merged


def _leader_dept_pks(user):
    """用户作为 leader 的启用部门及其全部下级并集（无主管职责返回空）。"""
    if user is None or not hasattr(user, "leader_depts"):
        return []
    pks = []
    for dept in user.leader_depts.filter(is_active=True):
        pks.extend(DeptInfo.recursion_dept_info(dept.pk))
    return pks


def _leader_user_pks(user):
    dept_pks = _leader_dept_pks(user)
    if not dept_pks:
        return []
    return list(UserInfo.objects.filter(dept__in=dept_pks).values_list("pk", flat=True))


def resolve_rule(rule, user):
    """单条规则 JSON → 归一化条件 dict（按规则类型注入用户/部门上下文，不改写传入 rule）。"""
    cond = {
        "field": rule.get("field"),
        "value": rule.get("value"),
        "match": rule.get("match", "exact"),
        "exclude": bool(rule.get("exclude")),
    }
    f_type = rule.get("type")

    if f_type == KeyChoices.ALL:
        cond["match"] = "all"
    elif f_type == KeyChoices.OWNER:
        cond["value"] = user.pk if user is not None else "0"
    elif f_type == KeyChoices.OWNER_DEPARTMENT:
        cond["value"] = user.dept_id if user is not None and user.dept_id else "0"
    elif f_type == KeyChoices.OWNER_DEPARTMENTS:
        cond["match"] = "in"
        cond["value"] = DeptInfo.recursion_dept_info(user.dept.pk) if user is not None and user.dept_id else []
    elif f_type == KeyChoices.DEPARTMENTS:
        cond["match"] = "in"
        cond["value"] = _flatten_dept_tree(cond["value"])
    elif f_type == KeyChoices.LEADER_DEPARTMENTS:
        cond["match"] = "in"
        cond["value"] = _leader_dept_pks(user)
    elif f_type == KeyChoices.LEADER_USERS:
        cond["match"] = "in"
        cond["value"] = _leader_user_pks(user)
    elif f_type in TABLE_TYPES:
        # 历史数据可能带 exact 占位，运行时统一按 in 编译（value 已归一化为 pk 数组）
        cond["match"] = "in"
        cond["value"] = _pk_list(cond["value"])
    elif f_type == KeyChoices.DATE:
        seconds = _json_value(cond["value"])
        if isinstance(seconds, (int, float)):
            delta = datetime.timedelta(seconds=abs(seconds))
            cond["value"] = timezone.now() - delta if seconds < 0 else timezone.now() + delta
    elif f_type == KeyChoices.DATETIME_RANGE:
        pair = cond["value"]
        if isinstance(pair, list) and len(pair) == 2:
            cond["value"] = [
                from_current_timezone(parse_datetime(pair[0])),
                from_current_timezone(parse_datetime(pair[1])),
            ]
    elif f_type == KeyChoices.DATETIME:
        if isinstance(cond["value"], str):
            cond["value"] = from_current_timezone(parse_datetime(cond["value"]))
    elif f_type == KeyChoices.JSON:
        cond["value"] = _json_value(cond["value"])
    return cond


def ip_in_q(name, val):
    """IP 匹配符：精确 / 前缀 / 网段 / 范围；``"*"`` 表示不限（守卫已修复，不再要求嵌套列表形态）。"""
    q = Q()
    if isinstance(val, str):
        val = [val]
    if "*" in val:
        return Q()
    for ip in val:
        if not ip:
            continue
        try:
            if "/" in ip:
                # 显式 list：hosts() 是一次性生成器，编译探测（_is_compilable）会消费它
                q |= Q(**{f"{name}__in": list(ipaddress.ip_network(ip).hosts())})
            elif "-" in ip:
                start_ip, end_ip = ip.split("-")
                q |= Q(**{f"{name}__range": (ipaddress.ip_address(start_ip), ipaddress.ip_address(end_ip))})
            elif len(ip.split(".")) == 4:
                q |= Q(**{f"{name}__exact": ip})
            else:
                q |= Q(**{f"{name}__startswith": ip})
        except ValueError:
            continue
    return q


def rule_to_q(rule):
    """归一化条件 dict → 单个 Q。

    ``match=all`` 的恒真语义由 compile_condition / build_rules_qs 前置短路，
    此处兜底返回 Q()，防止被直接调用时拼出 ``Q(field__all=...)``。
    """
    name = rule.get("field")
    val = rule.get("value")
    match = rule.get("match", "exact")
    if name is None or val is None:
        return Q()
    if match == "all":
        return Q()

    if match == "ip_in":
        q = ip_in_q(name, val)
    elif match == "m2m_all":
        # 包含全部：逐值取交集（AND），不再依赖外层组合模式
        values = val if isinstance(val, (list, tuple)) else [val]
        q = Q()
        for v in values:
            q &= Q(**{f"{name}__in": [v]})
    elif match in ("m2m", "in"):
        values = val if isinstance(val, (list, tuple)) else [val]
        q = Q() if "*" in values else Q(**{f"{name}__in": values})
    elif match == "regex":
        try:
            re.compile(val)
            q = Q(**{f"{name}__regex": val})
        except re.error:
            q = Q(pk__isnull=True)
    else:
        if val == "*":
            q = Q()
        else:
            normalized = normalize_match_value(match, val)
            if normalized is None:
                # 严格类型 lookup 的非法形态：恒假（与非法正则同口径），不留给 SQL 编译期抛错
                q = Q(pk__isnull=True)
            else:
                q = Q(**{f"{name}__{match}": normalized})

    if rule.get("exclude"):
        q = ~q
    return q


def build_rules_qs(rules):
    """批量入口（RelatedManager.get_filter_attrs_qs 委托）：每条规则一个 Q，无效规则跳过。

    ALL 短路按 ``type`` 判定而非 ``match`` 字面（存量 ALL 规则的 match 可能缺失/为 ``"*"``），
    与 resolve_rule 的读侧口径一致。
    """
    result = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if rule.get("type") == KeyChoices.ALL or rule.get("match") == "all":
            result.append(Q())
            continue
        if rule.get("field") is None or rule.get("value") is None:
            continue
        result.append(rule_to_q(rule))
    return result
