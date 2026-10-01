#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动态表单公式字段：表达式解析与求值（纯函数，无 DB；前后端同口径）。

语法 v1（前端镜像 ``src/views/form/my/utils/formula.ts``，结构一一对应）::

    expr    := term (('+' | '-') term)*
    term    := unary (('*' | '/') unary)*
    unary   := '-' unary | primary
    primary := NUMBER | REF | CALL | '(' expr ')'
    REF     := '{' key '}' | '{' table '.' column '}'
    CALL    := FUNC '(' expr (',' expr)* ')'

语义约定（两端必须一致，改语义须同时改前端并跑同口径向量测试）：

- 标量引用 ``{key}`` 读取同类字段值：None/缺失/非数值（含布尔与字符串）→ None；
- 表格列引用 ``{table.column}`` **只能**作为 SUM/AVG/MIN/MAX 的参数：逐行取列值，
  忽略 None 与非数值；SUM 空集合为 0，AVG/MIN/MAX 空集合为 None；
- 二元运算任一操作数为 None → None；除数为 0 或 None → None；
- ROUND(x[, n])：n 为 0-6 整数字面量（缺省 2）；ABS(x)；参数为 None → None；
- 结果统一 round 到 6 位小数（消除浮点尾差；half up 语义与 JS ``Math.round``
  一致，即 ``floor(x + 0.5)``）；非有限值（inf/nan）与超大数（|x| ≥ 1e18）不 round，
  溢出为非有限值 → None；
- 幂等累加顺序：SUM/AVG 按行序逐项相加（不用 math.fsum——与 JS 累加顺序一致
  才能保证双端结果 bit 级相同）；
- 嵌套公式按依赖顺序求值（被引用公式取已 round 的终值）；公式自引用与循环引用
  在 schema 校验期拒绝。

安全边界：不使用 eval（显式 tokenizer + 递归下降）；表达式长度与嵌套深度封顶；
函数与引用白名单；表格列引用仅限聚合语境。
"""

import math
from collections.abc import Callable
from typing import Any

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from dataset.utils.dform_formula_parser import (  # noqa: F401  (解析器拆至 dform_formula_parser，此处再导出保持调用面)
    AGGREGATE_FUNCS,
    MAX_FORMULA_DEPTH,
    MAX_FORMULA_LENGTH,
    ROUND_DIGITS_DEFAULT,
    ROUND_DIGITS_MAX,
    SCALAR_FUNCS,
    parse_formula,
)

# ---------------------------------------------------------------------------
# 引用与语义
# ---------------------------------------------------------------------------


def formula_references(node: dict) -> list[tuple[str, str | None]]:
    """展开表达式引用：``(key, None)`` 为标量引用，``(table, column)`` 为表格列引用。"""
    found: list[tuple[str, str | None]] = []

    def walk(item: dict) -> None:
        kind = item["kind"]
        if kind == "ref":
            found.append((item["key"], None))
        elif kind == "col":
            found.append((item["table"], item["column"]))
        elif kind == "unary":
            walk(item["operand"])
        elif kind == "binary":
            walk(item["left"])
            walk(item["right"])
        elif kind == "call":
            for arg in item["args"]:
                walk(arg)

    walk(node)
    return found


def _scalar(value: Any) -> float | None:
    """参与计算的标量：仅 int/float（布尔排除），其余为 None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _round_six(value: float) -> float | None:
    """round 到 6 位小数（half up，与 JS Math.round 同语义）；非有限值 → None。"""
    if not math.isfinite(value):
        return None
    if abs(value) >= 1e18:  # 大数无浮点尾差问题，避免 round 引入额外误差
        return value
    return math.floor(value * 1e6 + 0.5) / 1e6


ScalarResolver = Callable[[str], float | None]
RowResolver = Callable[[str], list]


def _aggregate(name: str, node: dict, rows_of: RowResolver) -> float | None:
    values: list[float] = []
    for row in rows_of(node["table"]):
        if isinstance(row, dict):
            number = _scalar(row.get(node["column"]))
            if number is not None:
                values.append(number)
    if name == "SUM":
        total = 0.0
        for value in values:  # 按行序累加：与 JS 端同序，结果 bit 级一致
            total += value
        return total
    if not values:
        return None
    if name == "AVG":
        total = 0.0
        for value in values:
            total += value
        return total / len(values)
    if name == "MIN":
        return min(values)
    return max(values)


def _eval(node: dict, scalar_of: ScalarResolver, rows_of: RowResolver) -> float | None:
    kind = node["kind"]
    if kind == "num":
        return node["value"]
    if kind == "ref":
        return scalar_of(node["key"])
    if kind == "col":  # 解析期已保证只在聚合参数位，此处为防御
        return None
    if kind == "unary":
        value = _eval(node["operand"], scalar_of, rows_of)
        return None if value is None else -value
    if kind == "binary":
        left = _eval(node["left"], scalar_of, rows_of)
        right = _eval(node["right"], scalar_of, rows_of)
        if left is None or right is None:
            return None
        if node["op"] == "+":
            return left + right
        if node["op"] == "-":
            return left - right
        if node["op"] == "*":
            return left * right
        if right == 0:
            return None
        return left / right
    if kind == "call":
        name = node["name"]
        if name in AGGREGATE_FUNCS:
            return _aggregate(name, node["args"][0], rows_of)
        value = _eval(node["args"][0], scalar_of, rows_of)
        if value is None:
            return None
        if name == "ABS":
            return abs(value)
        digits = ROUND_DIGITS_DEFAULT
        if len(node["args"]) == 2:
            digits = int(node["args"][1]["value"])
        factor = 10**digits
        if abs(value) >= 1e18 / factor:
            return value
        return math.floor(value * factor + 0.5) / factor
    return None


def evaluate_formula(node: dict, scalar_of: ScalarResolver, rows_of: RowResolver) -> float | None:
    """求值单个表达式（结果已 round 到 6 位小数；None 表示空/不可计算）。"""
    value = _eval(node, scalar_of, rows_of)
    if value is None:
        return None
    return _round_six(value)


def evaluate_formula_fields(fields: list[dict], data: dict, hidden: set[str] | None = None) -> dict:
    """按依赖顺序求值全部 formula 字段，返回 ``{key: 值}``（含 None）。

    ``data`` 为规范化后的提交数据（非公式字段的终值）；被隐藏（联动）的
    formula 字段结果为 None；被引用的公式字段取已 round 的终值（级联求值）。
    公式自引用与循环引用在 schema 校验期拒绝，此处仅作防御性兜底。
    """
    hidden = hidden or set()
    declarations = {item["key"]: item for item in fields if item.get("type") == "formula"}
    results: dict[str, float | None] = {}
    resolving: set[str] = set()

    def rows_of(table_key: str) -> list:
        rows = data.get(table_key)
        return rows if isinstance(rows, list) else []

    def resolve_formula(key: str) -> float | None:
        if key in results:
            return results[key]
        if key in resolving:
            return None
        item = declarations[key]
        if key in hidden:
            results[key] = None
            return None
        resolving.add(key)
        try:
            node = parse_formula(item.get("formula") or "")
            value = evaluate_formula(node, scalar_of, rows_of)
        except ValidationError:
            value = None
        finally:
            resolving.discard(key)
        results[key] = value
        return value

    def scalar_of(key: str) -> float | None:
        if key in declarations:
            return resolve_formula(key)
        return _scalar(data.get(key))

    for key in declarations:
        resolve_formula(key)
    return results


# ---------------------------------------------------------------------------
# schema 校验（引用合法性 + 循环引用）
# ---------------------------------------------------------------------------


def validate_formula_fields(fields: list[dict]) -> None:
    """校验全部公式字段：引用存在且类型可算、表格列引用有效、无循环引用。"""
    numeric_types = {"number", "amount", "formula"}
    fields_by_key = {item["key"]: item for item in fields}
    table_columns: dict[str, set[str]] = {}
    for item in fields:
        if item.get("type") == "table":
            table_columns[item["key"]] = {
                column["key"] for column in item.get("columns") or [] if column.get("type") == "number"
            }
    dependencies: dict[str, set[str]] = {}
    for item in fields:
        if item.get("type") != "formula":
            continue
        key = item["key"]
        node = parse_formula(item.get("formula"))
        deps: set[str] = set()
        for ref_key, column in formula_references(node):
            if column is None:
                if ref_key == key:
                    raise ValidationError(_("A formula field cannot reference itself: {}").format(key))
                target = fields_by_key.get(ref_key)
                if target is None:
                    raise ValidationError(_("Formula references an unknown field: {}").format(ref_key))
                if target.get("type") not in numeric_types:
                    raise ValidationError(_("Formula can only reference numeric fields: {}").format(ref_key))
                deps.add(ref_key)
            else:
                if ref_key not in table_columns:
                    raise ValidationError(_("Formula references an unknown table: {}").format(ref_key))
                if column not in table_columns[ref_key]:
                    raise ValidationError(
                        _("Formula references an invalid table column: {}.{}").format(ref_key, column)
                    )
        dependencies[key] = deps
    _assert_acyclic(dependencies)


def _assert_acyclic(graph: dict[str, set[str]]) -> None:
    """公式依赖图环检测（DFS 三色标记；环上字段链随报错给出）。"""
    state: dict[str, int] = {}

    def visit(node: str, path: list[str]) -> None:
        state[node] = 1  # GRAY
        for nxt in sorted(graph.get(node) or ()):
            if nxt not in graph:
                continue  # 非公式依赖
            mark = state.get(nxt, 0)
            if mark == 1:
                chain = path[path.index(nxt) :] + [nxt] if nxt in path else [nxt]
                raise ValidationError(_("Formula contains a circular reference: {}").format(" -> ".join(chain)))
            if mark == 0:
                visit(nxt, path + [nxt])
        state[node] = 2  # BLACK

    for key in graph:
        if state.get(key, 0) == 0:
            visit(key, [key])
