#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动态表单公式字段：表达式词法与语法（递归下降解析，自 dform_formula 拆分，行为不变）。

语法 v1（前端镜像 ``src/views/form/my/utils/formula.ts``，结构一一对应）::

    expr    := term (('+' | '-') term)*
    term    := unary (('*' | '/') unary)*
    unary   := '-' unary | primary
    primary := NUMBER | REF | CALL | '(' expr ')'
    REF     := '{' key '}' | '{' table '.' column '}'
    CALL    := FUNC '(' expr (',' expr)* ')'

安全边界：不使用 eval（显式 tokenizer + 递归下降）；表达式长度与嵌套深度封顶；
函数与引用白名单；表格列引用仅限聚合语境。语义约定与求值见 ``dform_formula``。
"""

import re
from functools import lru_cache
from typing import Any

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

MAX_FORMULA_LENGTH = 500
MAX_FORMULA_DEPTH = 25
ROUND_DIGITS_MAX = 6
ROUND_DIGITS_DEFAULT = 2

#: 聚合函数：参数必须是表格列引用
AGGREGATE_FUNCS = ("SUM", "AVG", "MIN", "MAX")
#: 普通函数：(最小参数数, 最大参数数)
SCALAR_FUNCS = {"ROUND": (1, 2), "ABS": (1, 1)}

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _syntax_error() -> ValidationError:
    return ValidationError(_("Formula expression is invalid"))


# ---------------------------------------------------------------------------
# 词法
# ---------------------------------------------------------------------------


def _tokenize(expression: str) -> list[tuple[str, Any]]:
    tokens: list[tuple[str, Any]] = []
    index, length = 0, len(expression)
    while index < length:
        char = expression[index]
        if char.isspace():
            index += 1
            continue
        if char.isdigit():
            match = _NUMBER_RE.match(expression, index)
            assert match is not None  # 首字符是数字，正则必命中
            tokens.append(("num", float(match.group())))
            index = match.end()
            continue
        if char.isalpha() or char == "_":
            match = _IDENT_RE.match(expression, index)
            assert match is not None
            tokens.append(("ident", match.group()))
            index = match.end()
            continue
        if char in "{}.,()+-*/":
            tokens.append((char, char))
            index += 1
            continue
        raise _syntax_error()
    tokens.append(("eof", None))
    return tokens


# ---------------------------------------------------------------------------
# 语法（递归下降）
# ---------------------------------------------------------------------------


def _check_call(name: str, args: list[dict]) -> dict:
    if name in AGGREGATE_FUNCS:
        if len(args) != 1 or args[0]["kind"] != "col":
            raise ValidationError(_("SUM/AVG/MIN/MAX require a table column reference"))
        return {"kind": "call", "name": name, "args": args}
    if name not in SCALAR_FUNCS:
        raise ValidationError(_("Unknown formula function: {}").format(name))
    low, high = SCALAR_FUNCS[name]
    if not low <= len(args) <= high:
        raise ValidationError(_("Formula function {} expects {} argument(s)").format(name, f"{low}-{high}"))
    if name == "ROUND" and len(args) == 2:
        digits = args[1]
        if (
            digits["kind"] != "num"
            or digits["value"] != int(digits["value"])
            or not 0 <= digits["value"] <= ROUND_DIGITS_MAX
        ):
            raise ValidationError(_("ROUND digits must be an integer between 0 and {}").format(ROUND_DIGITS_MAX))
    return {"kind": "call", "name": name, "args": args}


class _Parser:
    def __init__(self, tokens: list[tuple[str, Any]]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> tuple[str, Any]:
        return self.tokens[self.pos]

    def next(self) -> tuple[str, Any]:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def expect(self, kind: str) -> tuple[str, Any]:
        token = self.peek()
        if token[0] != kind:
            raise _syntax_error()
        return self.next()

    def parse(self) -> dict:
        node = self.expr(0)
        if self.peek()[0] != "eof":
            raise _syntax_error()
        return node

    def expr(self, depth: int) -> dict:
        node = self.term(depth)
        while self.peek()[0] in ("+", "-"):
            op = self.next()[0]
            node = {"kind": "binary", "op": op, "left": node, "right": self.term(depth)}
        return node

    def term(self, depth: int) -> dict:
        node = self.unary(depth)
        while self.peek()[0] in ("*", "/"):
            op = self.next()[0]
            node = {"kind": "binary", "op": op, "left": node, "right": self.unary(depth)}
        return node

    def unary(self, depth: int) -> dict:
        if self.peek()[0] == "-":
            self.next()
            return {"kind": "unary", "op": "-", "operand": self.unary(depth)}
        return self.primary(depth)

    def primary(self, depth: int) -> dict:
        if depth > MAX_FORMULA_DEPTH:
            raise ValidationError(_("Formula expression is too deeply nested"))
        kind, value = self.peek()
        if kind == "num":
            self.next()
            return {"kind": "num", "value": value}
        if kind == "{":
            return self.ref()
        if kind == "ident":
            self.next()
            return self.call(value.upper(), depth)
        if kind == "(":
            self.next()
            node = self.expr(depth + 1)
            self.expect(")")
            return node
        raise _syntax_error()

    def ref(self) -> dict:
        self.expect("{")
        _, key = self.expect("ident")
        if self.peek()[0] == ".":
            self.next()
            _, column = self.expect("ident")
            self.expect("}")
            return {"kind": "col", "table": key, "column": column}
        self.expect("}")
        return {"kind": "ref", "key": key}

    def call(self, name: str, depth: int) -> dict:
        self.expect("(")
        args: list[dict] = []
        if self.peek()[0] != ")":
            args.append(self.expr(depth + 1))
            while self.peek()[0] == ",":
                self.next()
                args.append(self.expr(depth + 1))
        self.expect(")")
        return _check_call(name, args)


def _assert_column_usage(node: dict, allow_column: bool = False) -> None:
    """表格列引用仅允许出现在聚合函数参数位（其余语境的列引用语义不明，拒绝）。"""
    kind = node["kind"]
    if kind == "col":
        if not allow_column:
            raise ValidationError(_("A table column reference must be used inside SUM/AVG/MIN/MAX"))
        return
    if kind == "call":
        if node["name"] in AGGREGATE_FUNCS:
            return  # 参数为 col（_check_call 已保证）
        for arg in node["args"]:
            _assert_column_usage(arg)
        return
    if kind == "unary":
        _assert_column_usage(node["operand"])
        return
    if kind == "binary":
        _assert_column_usage(node["left"])
        _assert_column_usage(node["right"])


@lru_cache(maxsize=512)
def parse_formula(expression: str) -> dict:
    """语法校验并解析公式表达式（结果缓存；AST 只读，求值不修改）。"""
    if not isinstance(expression, str) or not expression.strip():
        raise ValidationError(_("Formula expression is required"))
    if len(expression) > MAX_FORMULA_LENGTH:
        raise ValidationError(_("Formula expression is too long (max {} characters)").format(MAX_FORMULA_LENGTH))
    node = _Parser(_tokenize(expression)).parse()
    _assert_column_usage(node)
    return node
