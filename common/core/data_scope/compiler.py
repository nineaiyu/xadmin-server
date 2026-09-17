#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""数据权限编译器：Q 编译、授予组合与写入校验。"""

import json

from django.apps import apps
from django.core.exceptions import EmptyResultSet, FieldDoesNotExist, FieldError, FullResultSet
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

from common.utils import get_logger

from .constants import RUNTIME_VALUE_TYPES, SPECIAL_MATCHES, TABLE_TYPES, KeyChoices
from .values import (
    ALLOW_ALL,
    DENY_ALL,
    _json_value,
    _pk_list,
    combine,
    condition_result,
    normalize_match_value,
    resolve_rule,
    rule_to_q,
)

logger = get_logger(__name__)


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
                    raise ValidationError(_("Rule %(index)d has a malformed JSON value") % {"index": index}) from None
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
