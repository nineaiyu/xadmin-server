# -*- coding: utf-8 -*-
"""动态表单公式字段：解析/求值（纯函数）与 schema/提交校验集成。

公式语义与前端镜像（src/views/form/my/utils/formula.ts）同口径——
本文件的求值向量是双端共享口径的服务端侧锚点（前端单测使用同一组用例）。
"""

import pytest
from django.core.exceptions import ValidationError

from dataset.utils.dform import validate_schema, validate_submission_data
from dataset.utils.dform_filter import build_filter_data, coerce_filter_value
from dataset.utils.dform_formula import (
    MAX_FORMULA_LENGTH,
    evaluate_formula,
    evaluate_formula_fields,
    formula_references,
    parse_formula,
)

# ---------------------------------------------------------------------------
# 纯求值
# ---------------------------------------------------------------------------


def _eval(expression, data=None):
    return evaluate_formula(
        parse_formula(expression), lambda key: _scalar_of(data or {}, key), lambda table: _rows_of(data or {}, table)
    )


def _scalar_of(data, key):
    from dataset.utils.dform_formula import _scalar

    return _scalar(data.get(key))


def _rows_of(data, table):
    rows = data.get(table)
    return rows if isinstance(rows, list) else []


class TestEvaluate:
    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("1 + 2 * 3", 7.0),
            ("(1 + 2) * 3", 9.0),
            ("10 / 4", 2.5),
            ("-2 + 5", 3.0),
            ("2 * -3", -6.0),
            ("1 - -1", 2.0),
            ("0.1 + 0.2", 0.3),  # 六位小数 round 消除浮点尾差
            ("10 / 3", 3.333333),
        ],
    )
    def test_arithmetic(self, expression, expected):
        assert _eval(expression) == expected

    def test_field_reference(self):
        assert _eval("{price} * {qty}", {"price": 3, "qty": 4}) == 12.0

    def test_missing_or_invalid_operand_is_none(self):
        assert _eval("{price} + 1", {"price": None}) is None
        assert _eval("{price} + 1", {"price": "abc"}) is None
        assert _eval("1 + {missing}") is None
        assert _eval("{flag} + 1", {"flag": True}) is None  # 布尔不是数值
        with pytest.raises(ValidationError):
            _eval("1 + true", {})  # 不支持布尔字面量（true 被当函数名解析）

    def test_division_by_zero_is_none(self):
        assert _eval("1 / 0") is None
        assert _eval("1 / {zero}", {"zero": 0}) is None
        assert _eval("1 / {none}", {"none": None}) is None

    def test_round_and_abs(self):
        assert _eval("ROUND(1.2345, 2)") == 1.23
        assert _eval("ROUND(1.5)") == 1.5
        assert _eval("round(2.5, 0)") == 3.0  # 函数名大小写不敏感；half up
        assert _eval("ABS(-3.2)") == 3.2
        assert _eval("ABS({x})", {"x": -1}) == 1.0

    def test_round_digits_bounds(self):
        with pytest.raises(ValidationError):
            parse_formula("ROUND(1, 7)")
        with pytest.raises(ValidationError):
            parse_formula("ROUND(1, 1.5)")
        with pytest.raises(ValidationError):
            parse_formula("ROUND(1, -1)")

    def test_aggregate_functions(self):
        data = {"items": [{"price": 1}, {"price": 2.5}, {"price": None}, {"price": "x"}, {}]}
        assert _eval("SUM({items.price})", data) == 3.5
        assert _eval("AVG({items.price})", data) == 1.75
        assert _eval("MIN({items.price})", data) == 1.0
        assert _eval("MAX({items.price})", data) == 2.5
        assert _eval("SUM({items.price}) * 2", data) == 7.0

    def test_aggregate_empty_table(self):
        assert _eval("SUM({items.price})", {"items": []}) == 0.0
        assert _eval("AVG({items.price})", {"items": []}) is None
        assert _eval("MIN({items.price})", {"items": []}) is None
        assert _eval("MAX({items.price})", {"items": []}) is None

    def test_references(self):
        node = parse_formula("{a} + SUM({t.c})")
        assert set(formula_references(node)) == {("a", None), ("t", "c")}


class TestSyntaxErrors:
    @pytest.mark.parametrize(
        "expression",
        [
            "1 +",
            "{}",
            "{a",
            "{a.}",
            "* 2",
            "1 2",
            "SUM(1)",  # 聚合参数必须是表格列引用
            "{t.c} + 1",  # 列引用只能出现在聚合内
            "UNKNOWN(1)",
            "ROUND()",
            "ROUND(1, 2, 3)",
            "1 & 2",
            "'abc'",
        ],
    )
    def test_invalid_syntax(self, expression):
        with pytest.raises(ValidationError):
            parse_formula(expression)

    def test_empty_and_too_long(self):
        with pytest.raises(ValidationError):
            parse_formula("   ")
        with pytest.raises(ValidationError):
            parse_formula("1 + " * (MAX_FORMULA_LENGTH // 4) + "1")

    def test_depth_limit(self):
        with pytest.raises(ValidationError):
            parse_formula("(" * 30 + "1" + ")" * 30)


# ---------------------------------------------------------------------------
# schema 校验
# ---------------------------------------------------------------------------


def _field(key, ftype="input", **extra):
    item = {"key": key, "label": key, "type": ftype}
    item.update(extra)
    return item


class TestSchemaValidation:
    def test_valid_formula_schema(self):
        schema = {
            "fields": [
                _field("price", "number"),
                _field("qty", "amount"),
                _field("items", "table", columns=[{"key": "amount", "label": "金额", "type": "number"}]),
                _field("total", "formula", formula="SUM({items.amount})"),
                _field("grand", "formula", formula="{price} * {qty} + {total}", precision=2),
            ]
        }
        fields = validate_schema(schema)
        assert [item["type"] for item in fields][-2:] == ["formula", "formula"]

    def test_expression_required(self):
        with pytest.raises(ValidationError):
            validate_schema({"fields": [_field("f", "formula")]})
        with pytest.raises(ValidationError):
            validate_schema({"fields": [_field("f", "formula", formula="  ")]})

    def test_formula_cannot_be_required(self):
        with pytest.raises(ValidationError):
            validate_schema({"fields": [_field("f", "formula", formula="1 + 1", required=True)]})

    def test_unknown_reference(self):
        with pytest.raises(ValidationError):
            validate_schema({"fields": [_field("f", "formula", formula="{ghost} + 1")]})

    def test_reference_must_be_numeric(self):
        with pytest.raises(ValidationError):
            validate_schema({"fields": [_field("name"), _field("f", "formula", formula="{name} + 1")]})

    def test_reference_table_column_rules(self):
        # 表格不存在
        with pytest.raises(ValidationError):
            validate_schema({"fields": [_field("f", "formula", formula="SUM({ghost.x})")]})
        # 列不是数值列
        with pytest.raises(ValidationError):
            validate_schema(
                {
                    "fields": [
                        _field("items", "table", columns=[{"key": "note", "label": "备注", "type": "input"}]),
                        _field("f", "formula", formula="SUM({items.note})"),
                    ]
                }
            )
        # 列不存在
        with pytest.raises(ValidationError):
            validate_schema(
                {
                    "fields": [
                        _field("items", "table", columns=[{"key": "amount", "label": "金额", "type": "number"}]),
                        _field("f", "formula", formula="SUM({items.ghost})"),
                    ]
                }
            )

    def test_self_reference_rejected(self):
        with pytest.raises(ValidationError):
            validate_schema({"fields": [_field("f", "formula", formula="{f} + 1")]})

    def test_circular_reference_rejected(self):
        with pytest.raises(ValidationError):
            validate_schema(
                {
                    "fields": [
                        _field("a", "formula", formula="{b} + 1"),
                        _field("b", "formula", formula="{a} + 1"),
                    ]
                }
            )

    def test_precision_bounds(self):
        with pytest.raises(ValidationError):
            validate_schema({"fields": [_field("f", "formula", formula="1 + 1", precision=9)]})

    def test_filterable_allowed(self):
        fields = validate_schema({"fields": [_field("f", "formula", formula="1 + 1", filterable=True)]})
        assert fields[0]["filterable"] is True


# ---------------------------------------------------------------------------
# 提交校验（求值覆盖 / 联动隐藏 / 落库物化）
# ---------------------------------------------------------------------------


def _submission_schema():
    return {
        "fields": [
            _field("price", "number"),
            _field("qty", "number"),
            _field("items", "table", columns=[{"key": "amount", "label": "金额", "type": "number"}]),
            _field("total", "formula", formula="{price} * {qty}", precision=2),
            _field("sum_amount", "formula", formula="SUM({items.amount})", filterable=True),
        ]
    }


class TestSubmission:
    def test_formula_recomputed_server_side(self):
        data = validate_submission_data(
            _submission_schema(),
            {"price": 3, "qty": 4, "items": [], "total": 999, "sum_amount": 888},
        )
        # 客户端提交值被忽略，服务端重算
        assert data["total"] == 12.0
        assert data["sum_amount"] == 0.0

    def test_hidden_formula_is_none(self):
        schema = _submission_schema()
        schema["linkages"] = [{"target": "total", "field": "qty", "op": "eq", "value": 0, "effect": "hide"}]
        data = validate_submission_data(schema, {"price": 3, "qty": 0, "items": []})
        assert data["total"] is None
        assert data["sum_amount"] == 0.0

    def test_formula_depending_on_hidden_field_is_none(self):
        schema = _submission_schema()
        schema["linkages"] = [{"target": "price", "field": "qty", "op": "eq", "value": 0, "effect": "hide"}]
        data = validate_submission_data(schema, {"price": 3, "qty": 0, "items": []})
        assert data["price"] is None
        assert data["total"] is None

    def test_nested_formula_uses_rounded_value(self):
        schema = {
            "fields": [
                _field("base", "number"),
                _field("a", "formula", formula="{base} * 0.1"),
                _field("b", "formula", formula="{a} * 3"),
            ]
        }
        data = validate_submission_data(schema, {"base": 1})
        assert data["a"] == 0.1
        assert data["b"] == 0.3  # 取已 round 的引用值（0.1 * 3），非 0.30000000000000004

    def test_evaluate_formula_fields_with_hidden(self):
        fields = [
            _field("a", "formula", formula="1 + 1"),
            _field("b", "formula", formula="{a} + 1"),
        ]
        results = evaluate_formula_fields(fields, {}, {"a"})
        assert results["a"] is None
        assert results["b"] is None


class TestMaterializedFilter:
    def test_formula_value_materialized(self):
        schema = _submission_schema()
        data = {"price": 3, "qty": 4, "items": [], "total": 12.0, "sum_amount": 0.0}
        materialized = build_filter_data(schema, data)
        assert materialized == {"sum_amount": 0.0}  # 只有勾选 filterable 的公式字段
        # 数值筛选值规范化与物化值同形态
        item = {"key": "sum_amount", "type": "formula"}
        assert coerce_filter_value(item, "0") == 0
        assert coerce_filter_value(item, 0.0) == 0.0
        with pytest.raises(ValidationError):
            coerce_filter_value(item, "abc")
