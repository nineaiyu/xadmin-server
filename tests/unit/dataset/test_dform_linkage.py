# -*- coding: utf-8 -*-
"""动态表单联动规则单元测试（纯函数：校验 / 求值 / 提交应用）。

口径：
- 规则形态 {target, field, op, value?, effect}；op 白名单 eq/ne/in/notin/empty/notempty；
  effect 白名单 hide/show/require/optional；target/field 必须命中字段且不得自引用；
- 求值：按数组顺序**后者覆盖前者**（隐藏与必填两个维度独立覆盖）；
- 提交应用：隐藏字段跳过校验且不落库；require/optional 覆盖字段自身 required。
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils import translation

from dataset.utils.dform import (
    evaluate_linkages,
    normalize_schema,
    validate_linkages,
    validate_submission_data,
)


@pytest.fixture(autouse=True)
def _english_messages():
    """断言文案固定英文口径。

    校验消息走 i18n：本地编译过 .mo（含新增 zh 译文）后消息变中文，英文 match
    会失效；CI 无 .mo 时本来就是英文——显式 override 让两种环境同口径。
    """
    with translation.override("en"):
        yield


FIELDS = [
    {"key": "kind", "label": "类型", "type": "select", "options": ["A", "B"], "required": True},
    {"key": "amount", "label": "金额", "type": "number"},
    {"key": "reason", "label": "说明", "type": "input", "required": True},
    {"key": "tags", "label": "标签", "type": "checkbox", "options": ["x", "y"]},
]


def _schema(linkages=None):
    schema = {"fields": FIELDS}
    if linkages is not None:
        schema["linkages"] = linkages
    return schema


class TestValidateLinkages:
    def test_absent_or_empty(self):
        assert validate_linkages({}, FIELDS) == []
        assert validate_linkages({"linkages": []}, FIELDS) == []

    def test_normalize_and_drop_unknown_keys(self):
        rules = validate_linkages(
            {
                "linkages": [
                    {"target": "amount", "field": "kind", "op": "eq", "value": "A", "effect": "require", "x": 1}
                ]
            },
            FIELDS,
        )
        assert rules == [{"target": "amount", "field": "kind", "op": "eq", "value": "A", "effect": "require"}]

    def test_empty_op_has_no_value(self):
        rules = validate_linkages(
            {"linkages": [{"target": "reason", "field": "amount", "op": "empty", "effect": "hide"}]}, FIELDS
        )
        assert rules == [{"target": "reason", "field": "amount", "op": "empty", "effect": "hide"}]

    def test_target_and_trigger_must_be_fields(self):
        with pytest.raises(ValidationError, match="target"):
            validate_linkages(
                {"linkages": [{"target": "nope", "field": "kind", "op": "empty", "effect": "hide"}]}, FIELDS
            )
        with pytest.raises(ValidationError, match="trigger"):
            validate_linkages(
                {"linkages": [{"target": "amount", "field": "nope", "op": "empty", "effect": "hide"}]}, FIELDS
            )

    def test_self_reference_rejected(self):
        with pytest.raises(ValidationError, match="own trigger"):
            validate_linkages(
                {"linkages": [{"target": "amount", "field": "amount", "op": "empty", "effect": "hide"}]}, FIELDS
            )

    def test_unknown_op_and_effect(self):
        with pytest.raises(ValidationError, match="operator"):
            validate_linkages(
                {"linkages": [{"target": "amount", "field": "kind", "op": "gt", "value": 1, "effect": "hide"}]}, FIELDS
            )
        with pytest.raises(ValidationError, match="effect"):
            validate_linkages(
                {"linkages": [{"target": "amount", "field": "kind", "op": "empty", "effect": "boom"}]}, FIELDS
            )

    def test_value_required_for_valued_ops(self):
        with pytest.raises(ValidationError, match="linkage value"):
            validate_linkages(
                {"linkages": [{"target": "amount", "field": "kind", "op": "eq", "effect": "require"}]}, FIELDS
            )
        with pytest.raises(ValidationError, match="value list"):
            validate_linkages(
                {"linkages": [{"target": "amount", "field": "kind", "op": "in", "value": "A", "effect": "require"}]},
                FIELDS,
            )
        with pytest.raises(ValidationError, match="linkage value"):
            validate_linkages(
                {
                    "linkages": [
                        {"target": "amount", "field": "kind", "op": "eq", "value": {"a": 1}, "effect": "require"}
                    ]
                },
                FIELDS,
            )

    def test_too_many_rules(self):
        rules = [{"target": "amount", "field": "kind", "op": "empty", "effect": "hide"} for _ in range(51)]
        with pytest.raises(ValidationError, match="exceed"):
            validate_linkages({"linkages": rules}, FIELDS)

    def test_not_a_list(self):
        with pytest.raises(ValidationError, match="Invalid form linkages"):
            validate_linkages({"linkages": "oops"}, FIELDS)


class TestNormalizeSchema:
    def test_keeps_fields_and_linkages(self):
        schema = normalize_schema(
            {
                "fields": FIELDS,
                "linkages": [{"target": "reason", "field": "kind", "op": "eq", "value": "B", "effect": "optional"}],
                "unknown": 1,
            }
        )
        assert set(schema) == {"fields", "linkages"}
        assert len(schema["fields"]) == 4 and schema["linkages"][0]["effect"] == "optional"

    def test_linkages_omitted_when_absent(self):
        assert set(normalize_schema({"fields": FIELDS})) == {"fields"}


class TestEvaluateLinkages:
    def test_no_rules_keeps_defaults(self):
        state = evaluate_linkages(_schema(), {"kind": "A"})
        assert state["reason"] == {"hidden": False, "required": None}

    def test_operators(self):
        data_hide = _schema([{"target": "reason", "field": "kind", "op": "eq", "value": "A", "effect": "hide"}])
        assert evaluate_linkages(data_hide, {"kind": "A"})["reason"]["hidden"] is True
        assert evaluate_linkages(data_hide, {"kind": "B"})["reason"]["hidden"] is False
        ne = _schema([{"target": "reason", "field": "kind", "op": "ne", "value": "A", "effect": "hide"}])
        assert evaluate_linkages(ne, {"kind": "B"})["reason"]["hidden"] is True
        in_rule = _schema([{"target": "reason", "field": "kind", "op": "in", "value": ["A", "B"], "effect": "hide"}])
        assert evaluate_linkages(in_rule, {"kind": "B"})["reason"]["hidden"] is True
        notin_rule = _schema(
            [{"target": "reason", "field": "kind", "op": "notin", "value": ["A"], "effect": "require"}]
        )
        assert evaluate_linkages(notin_rule, {"kind": "B"})["reason"]["required"] is True
        empty_rule = _schema([{"target": "reason", "field": "amount", "op": "empty", "effect": "require"}])
        assert evaluate_linkages(empty_rule, {"amount": None})["reason"]["required"] is True
        notempty_rule = _schema([{"target": "reason", "field": "amount", "op": "notempty", "effect": "require"}])
        assert evaluate_linkages(notempty_rule, {"amount": 0})["reason"]["required"] is True

    def test_multi_value_trigger_uses_intersection(self):
        rule = _schema([{"target": "reason", "field": "tags", "op": "in", "value": ["y"], "effect": "require"}])
        assert evaluate_linkages(rule, {"tags": ["x", "y"]})["reason"]["required"] is True
        assert evaluate_linkages(rule, {"tags": ["x"]})["reason"]["required"] is None  # 未命中 = 沿用字段定义

    def test_last_match_wins_per_dimension(self):
        rules = [
            {"target": "reason", "field": "kind", "op": "notempty", "effect": "hide"},
            {"target": "reason", "field": "kind", "op": "eq", "value": "B", "effect": "show"},
            {"target": "reason", "field": "kind", "op": "eq", "value": "B", "effect": "optional"},
        ]
        state = evaluate_linkages(_schema(rules), {"kind": "B"})["reason"]
        assert state["hidden"] is False and state["required"] is False

    def test_bool_and_number_comparison(self):
        rule = _schema([{"target": "reason", "field": "amount", "op": "eq", "value": 3, "effect": "hide"}])
        assert evaluate_linkages(rule, {"amount": 3.0})["reason"]["hidden"] is True


class TestSubmissionWithLinkages:
    def test_hidden_field_skips_required_and_is_dropped(self):
        schema = _schema([{"target": "reason", "field": "kind", "op": "eq", "value": "A", "effect": "hide"}])
        normalized = validate_submission_data(schema, {"kind": "A", "reason": "随便写的"})
        assert normalized["reason"] is None, "隐藏字段不落库"

    def test_dynamic_require_enforced(self):
        schema = _schema([{"target": "amount", "field": "kind", "op": "eq", "value": "B", "effect": "require"}])
        with pytest.raises(ValidationError, match="金额"):
            validate_submission_data(schema, {"kind": "B", "reason": "ok"})
        normalized = validate_submission_data(schema, {"kind": "B", "reason": "ok", "amount": 10})
        assert normalized["amount"] == 10

    def test_dynamic_optional_relaxes_required(self):
        schema = _schema([{"target": "reason", "field": "kind", "op": "eq", "value": "B", "effect": "optional"}])
        normalized = validate_submission_data(schema, {"kind": "B"})
        assert normalized["reason"] is None

    def test_without_rules_behaviour_unchanged(self):
        with pytest.raises(ValidationError, match="说明"):
            validate_submission_data(_schema(), {"kind": "A"})
