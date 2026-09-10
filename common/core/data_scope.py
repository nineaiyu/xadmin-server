#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : data_scope
"""数据权限规则编译器。

四段管线：RuleSpec(JSON) --validate--> resolve --> Condition --compile--> Q --combine--> ScopeResult

与旧实现（common/core/filter.py 内联逻辑）的关键差异：
- 三值布尔代数替代 ``Q()`` / ``Q(id=0)`` 魔法值：ALLOW_ALL（恒真）/ DENY_ALL（恒假）/ 条件 Q，
  「全部数据」规则在任何组合层级都正确生效（旧实现在且模式下会反转为全拒绝）；
- 纯函数实现，不就地改写传入的 rules（JSONField 反序列化出的同一 Python 对象）；
- 读侧字段兜底：存量坏规则（字段名写错等）编译为 DENY_ALL 并告警，不再让绑定用户的列表接口 500；
- ``ip_in`` 通配守卫修复（``"*" in val``）与 ``m2m_all`` 单 Q 化（包含全部不再依赖外层组合模式）。
"""

import datetime
import ipaddress
import json
import re
from functools import reduce

from django.apps import apps
from django.core.exceptions import EmptyResultSet, FieldDoesNotExist, FieldError, FullResultSet
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.forms.utils import from_current_timezone
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

from common.utils import get_logger
from system.services import DeptInfo, ModelLabelField, ModeTypeAbstract, UserInfo

logger = get_logger(__name__)

KeyChoices = ModelLabelField.KeyChoices
AND_MODE = ModeTypeAbstract.ModeChoices.AND
OR_MODE = ModeTypeAbstract.ModeChoices.OR

# value 原样存 pk 数组的规则类型（运行时强制按 in 匹配，历史数据可能带 exact 占位）
TABLE_TYPES = {
    KeyChoices.TABLE_USER,
    KeyChoices.TABLE_MENU,
    KeyChoices.TABLE_ROLE,
    KeyChoices.TABLE_DEPT,
}

# 编译器支持的匹配符（写入校验与编译共用同一集合）
SUPPORTED_MATCHES = frozenset(
    {
        "exact",
        "iexact",
        "contains",
        "icontains",
        "startswith",
        "istartswith",
        "endswith",
        "iendswith",
        "gt",
        "gte",
        "lt",
        "lte",
        "in",
        "regex",
        "m2m",
        "m2m_all",
        "all",
        "ip_in",
    }
)

# 框架自定义匹配符：不在 Django 字段 class lookups 里，须单独放行
SPECIAL_MATCHES = frozenset({"all", "m2m", "m2m_all", "ip_in"})

# value 在读取时由 resolve_rule 按当前用户/部门上下文注入的类型。
# 写入侧存的 value 只是占位（历史数据常见 ""/*），因此写入校验不做 value 形态与编译探测，
# 避免「管理员打开即改即报错」（只对 match 做校验）。
RUNTIME_VALUE_TYPES = (
    frozenset(
        {
            KeyChoices.OWNER,
            KeyChoices.OWNER_DEPARTMENT,
            KeyChoices.OWNER_DEPARTMENTS,
            KeyChoices.DEPARTMENTS,
            KeyChoices.LEADER_DEPARTMENTS,
            KeyChoices.LEADER_USERS,
        }
    )
    | TABLE_TYPES
)

# 数字型日期分量 lookup（value 必须是可转 int）
_NUMERIC_LOOKUPS = frozenset(
    {"year", "iso_year", "month", "day", "week", "week_day", "iso_week_day", "quarter", "hour", "minute", "second"}
)

# Django 对 value 类型/形态有硬要求的匹配符：写入与读取都须先归一，归一失败即 fail-closed。
# 这类错误 ``filter()`` 探测不到（isnull 传字符串、range 传单元素要到 SQL 编译期才抛），
# 读取侧的兜底见 _is_compilable。
STRICT_VALUE_MATCHES = frozenset({"isnull", "range"}) | _NUMERIC_LOOKUPS


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


def _resolve_field(model, path):
    """dunder 路径 → 末端字段对象（``admin__dept`` → DeptInfo.dept；``pk`` 是主键别名）。

    解析失败返回 None（字段不存在）。每段若是关系字段则继续在其目标模型上解析下一段，
    与 Django 跨表 lookup 的路径语义一致。
    """
    current = model
    field = None
    for seg in str(path).split("__"):
        if seg == "pk":
            field = current._meta.pk
        else:
            try:
                field = current._meta.get_field(seg)
            except FieldDoesNotExist:
                return None
        if field is None:
            return None
        if field.is_relation and field.related_model is not None:
            current = field.related_model
    return field


def _field_exists(model, path):
    return _resolve_field(model, path) is not None


def _allowed_matches(model, field):
    """字段实际可用的匹配符 = Django class lookups（与前端 match 下拉同源）∪ 框架自定义匹配符。"""
    matches = set(SPECIAL_MATCHES)
    resolved = _resolve_field(model, field)
    if resolved is not None:
        matches.update(resolved.get_class_lookups())
    return matches


def _is_compilable(model, q):
    """把 Q 编译成 SQL 探测合法性（只构建查询，不连库、不执行）。

    单靠 ``Q()``/``filter()`` 无法暴露全部非法 value：``isnull`` 传字符串、``range``
    传单元素要到 SQL 编译期才抛错（ValueError / IndexError），而那时异常发生在序列化
    阶段，绑定用户直接 500。故在此提前编译一次，坏规则 fail-closed。
    """
    try:
        str(model._default_manager.filter(q).query)
    except (EmptyResultSet, FullResultSet):
        # 空集/全集是 where 编译的合法优化结果（如 in []），不是坏规则
        return True
    except (ValueError, TypeError, LookupError, IndexError, OverflowError, FieldError, DjangoValidationError) as exc:
        logger.warning(
            "data scope rule is not compilable, fail-closed. model:%s q:%s error:%s",
            model._meta.label_lower,
            q,
            exc,
        )
        return False
    return True


def compile_condition(model, cond):
    """归一化条件 + 目标模型 → ScopeResult；无效规则返回 None（不参与组合）。"""
    if cond.get("match") == "all":
        return ALLOW_ALL
    field = cond.get("field")
    if field is None or cond.get("value") is None:
        return None
    if not _field_exists(model, field):
        # 存量坏规则读侧兜底：fail-closed + 告警，避免绑定用户列表接口 500
        logger.warning(
            "data scope rule field not found, fail-closed. model:%s field:%s",
            model._meta.label_lower,
            field,
        )
        return DENY_ALL
    q = rule_to_q(cond)
    if not _is_compilable(model, q):
        return DENY_ALL
    return condition_result(q)


def compile_grant(dp, model, user):
    """单条 DataPermission → ScopeResult。

    组内规则先按 table 过滤（与当前模型无关的授权整组跳过，返回 None），
    剩余规则各自 resolve + compile 后按授权自身 mode_type 组合。
    """
    rules = [
        rule
        for rule in (dp.rules or [])
        if isinstance(rule, dict) and rule.get("table") in (model._meta.label_lower, "*")
    ]
    if not rules:
        return None
    parts = [compile_condition(model, resolve_rule(rule, user)) for rule in rules]
    parts = [part for part in parts if part is not None]
    if not parts:
        return None
    return combine(parts, dp.mode_type)


def _resolve_model(table):
    if table == "*":
        return None
    try:
        app_label, model_name = table.split(".", 1)
        return apps.get_model(app_label, model_name)
    except (ValueError, LookupError):
        return None


def validate_rules(rules):
    """写入侧结构校验：坏规则在保存时被拒，而不是让绑定用户用 500 发现。"""
    if not isinstance(rules, (list, tuple)) or not rules:
        raise ValidationError(_("The rule cannot be null"))
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            raise ValidationError(_("Rule %(index)d must be an object") % {"index": index})
        table = rule.get("table")
        if not table or not isinstance(table, str):
            raise ValidationError(_("Rule %(index)d is missing a valid table") % {"index": index})
        model = _resolve_model(table)
        if table != "*" and model is None:
            raise ValidationError(
                _("Rule %(index)d references an unknown table %(table)s") % {"index": index, "table": table}
            )

        f_type = rule.get("type")
        if f_type not in KeyChoices.values:
            raise ValidationError(_("Rule %(index)d has an unknown type") % {"index": index})

        exclude = rule.get("exclude", False)
        if not isinstance(exclude, bool):
            raise ValidationError(_("Rule %(index)d has a non-boolean exclude flag") % {"index": index})

        value = rule.get("value")
        field = rule.get("field")

        # 通配字段只对「全部数据」有效；先于 match 校验，避免报成语义无关的 unsupported match
        if field == "*" and f_type != KeyChoices.ALL:
            raise ValidationError(_("The wildcard field is only valid on all-data rules"))

        # 类型联动校验
        if f_type == KeyChoices.ALL:
            if exclude:
                raise ValidationError(_("Excluding all data is not a valid rule"))
            # resolve_rule 读侧强制 match="all"，match/value 形态对 ALL 无语义——
            # 存量种子历史值随意（"*"/""/缺失），跳过 match/value 校验避免编辑即报错
        else:
            if value is None:
                raise ValidationError(_("Rule %(index)d is missing a value") % {"index": index})
            if not field or not isinstance(field, str):
                raise ValidationError(_("Rule %(index)d is missing a valid field") % {"index": index})

        if f_type in TABLE_TYPES or f_type in (
            KeyChoices.DEPARTMENTS,
            KeyChoices.OWNER_DEPARTMENTS,
            KeyChoices.LEADER_DEPARTMENTS,
            KeyChoices.LEADER_USERS,
        ):
            if rule.get("match", "exact") not in ("in", "exact"):
                raise ValidationError(_("Rules of this type only support the in match"))
            if f_type in TABLE_TYPES or f_type == KeyChoices.DEPARTMENTS:
                # _pk_list 会把标量包成单元素列表，能表达「无值」的只有空集
                if not _pk_list(value):
                    raise ValidationError(_("Rule %(index)d requires at least one value") % {"index": index})
        elif f_type == KeyChoices.DATE:
            seconds = _json_value(value)
            if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
                raise ValidationError(_("Relative-time rules require a numeric value"))
        elif f_type == KeyChoices.DATETIME:
            if parse_datetime(str(value)) is None:
                raise ValidationError(_("Rule %(index)d has a malformed datetime") % {"index": index})
        elif f_type == KeyChoices.DATETIME_RANGE:
            if not (
                isinstance(value, (list, tuple)) and len(value) == 2 and all(parse_datetime(str(v)) for v in value)
            ):
                raise ValidationError(_("Rule %(index)d requires two datetimes") % {"index": index})
        elif f_type == KeyChoices.JSON:
            if isinstance(value, str):
                try:
                    json.loads(value)
                except (TypeError, ValueError):
                    raise ValidationError(_("Rule %(index)d has a malformed JSON value") % {"index": index})
        elif f_type != KeyChoices.ALL and table != "*":
            # 自由值类型的 match 校验：与前端 match 下拉同源
            # （字段 class lookups ∪ 框架自定义匹配符，见 _allowed_matches）；
            # table="*" 无单一模型/字段可依，与字段校验同口径跳过
            match = rule.get("match", "exact")
            if match not in _allowed_matches(model, field):
                raise ValidationError(
                    _("Rule %(index)d has an unsupported match %(match)s") % {"index": index, "match": match}
                )
            # 运行时注入 value 的类型（OWNER / 部门类）：写入侧 value 只是占位，
            # 形态与编译探测都会误伤（resolve_rule 读侧会覆写为真实 pk），故跳过。
            if f_type not in RUNTIME_VALUE_TYPES:
                # 严格类型 lookup（isnull/range/日期分量）的 value 形态此处直接拒绝；
                # 其余 value 用与读侧同一套编译探测兜底，坏值不再拖到查询期变 500
                if normalize_match_value(match, value) is None:
                    raise ValidationError(
                        _("Rule %(index)d has a malformed value for match %(match)s") % {"index": index, "match": match}
                    )
                if not _is_compilable(model, rule_to_q(rule)):
                    raise ValidationError(_("Rule %(index)d cannot be applied with the given value") % {"index": index})

        # 字段路径校验（dunder 逐段；table="*" 无单一模型可依，跳过模型校验）
        if field != "*" and table != "*" and not _field_exists(model, field):
            raise ValidationError(_("Field %(field)s does not exist on %(table)s") % {"field": field, "table": table})
