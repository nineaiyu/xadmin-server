#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""数据权限编译器：匹配符白名单与模式常量。"""

from system.services import ModelLabelField, ModeTypeAbstract

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
