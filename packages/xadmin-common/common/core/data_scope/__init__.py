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

本包按职责拆分（constants / values / compiler），对外 API 由本文件统一再导出，
导入路径保持 ``common.core.data_scope`` 不变。
"""

from .compiler import compile_condition, compile_grant, validate_rules
from .constants import (
    AND_MODE,
    OR_MODE,
    RUNTIME_VALUE_TYPES,
    SPECIAL_MATCHES,
    STRICT_VALUE_MATCHES,
    SUPPORTED_MATCHES,
    TABLE_TYPES,
    KeyChoices,
)
from .values import (
    ALLOW_ALL,
    DENY_ALL,
    ScopeResult,
    build_rules_qs,
    combine,
    condition_result,
    ip_in_q,
    normalize_match_value,
    resolve_rule,
    rule_to_q,
)

__all__ = [
    "ALLOW_ALL",
    "AND_MODE",
    "DENY_ALL",
    "OR_MODE",
    "RUNTIME_VALUE_TYPES",
    "SPECIAL_MATCHES",
    "STRICT_VALUE_MATCHES",
    "SUPPORTED_MATCHES",
    "TABLE_TYPES",
    "KeyChoices",
    "ScopeResult",
    "build_rules_qs",
    "combine",
    "compile_condition",
    "compile_grant",
    "condition_result",
    "ip_in_q",
    "normalize_match_value",
    "resolve_rule",
    "rule_to_q",
    "validate_rules",
]
