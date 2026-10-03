# -*- coding: utf-8 -*-
"""数据集列声明解析：模型字段 / JSON 路径 / 数值标注与失败面。"""

import pytest
from django.core.exceptions import ValidationError
from django.db.models import FloatField, TextField
from django.db.models.functions import Cast, Substr

from dataset.models.dform import DynamicFormSubmission
from dataset.utils.columns import (
    annotations_for,
    expression_of,
    json_fields_of,
    parse_column,
    resolve_columns,
    visible_root_of,
)
from system.models import ModelLabelField

pytestmark = pytest.mark.django_db

BOUND = "dataset.dynamicformsubmission"
MODEL = DynamicFormSubmission


@pytest.fixture
def whitelist(db):
    root, _ = ModelLabelField.objects.get_or_create(
        name=BOUND,
        defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "表单提交"},
    )
    for name in ("data", "schema_version", "created_time"):
        ModelLabelField.objects.get_or_create(
            name=name,
            parent=root,
            defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": name},
        )
    return {"data", "schema_version", "created_time"}


class TestParseColumn:
    def test_model_field(self, whitelist):
        spec = parse_column(MODEL, "created_time", whitelist)
        assert spec.is_json is False
        assert spec.alias == "created_time"
        assert visible_root_of(spec) == "created_time"

    def test_json_path(self, whitelist):
        spec = parse_column(MODEL, "data.kind", whitelist)
        assert spec.is_json is True
        assert (spec.root, spec.key) == ("data", "kind")
        assert spec.alias == "json_data_kind"
        assert spec.value_type == ""
        assert visible_root_of(spec) == "data"

    def test_json_path_with_number_annotation(self, whitelist):
        spec = parse_column(MODEL, "data.amount|number", whitelist)
        assert spec.raw == "data.amount|number"
        assert spec.path == "data.amount"
        assert spec.value_type == "number"
        expression = expression_of(spec)
        assert isinstance(expression, Cast)
        assert isinstance(expression.output_field, FloatField)

    def test_plain_json_path_casts_to_text(self, whitelist):
        # 文本列也必须 Cast：裸 KeyTextTransform 参与比较时值会被按 JSON 文档准备
        # （SQLite 报 malformed JSON）
        spec = parse_column(MODEL, "data.kind", whitelist)
        expression = expression_of(spec)
        assert isinstance(expression, Cast)
        assert isinstance(expression.output_field, TextField)

    def test_json_date_annotation(self, whitelist):
        spec = parse_column(MODEL, "data.deadline|date", whitelist)
        assert spec.value_type == "date"
        # 日期列保持文本（分桶走 Substr 前缀截断）
        expression = expression_of(spec)
        assert isinstance(expression, Cast)
        assert isinstance(expression.output_field, TextField)


class TestDateBucketExpression:
    def test_month_and_day_length(self, whitelist):
        from dataset.utils.columns import date_bucket_expression

        spec = parse_column(MODEL, "data.deadline|date", whitelist)
        month = date_bucket_expression(spec, "month")
        day = date_bucket_expression(spec, "day")
        assert isinstance(month, Substr)
        assert isinstance(day, Substr)
        # Substr 的截断长度是 source_expressions 的最后一项（Django 6 无 length 属性）
        assert month.source_expressions[-1].value == 7
        assert day.source_expressions[-1].value == 10

    def test_rejects_unknown_trunc(self, whitelist):
        from dataset.utils.columns import date_bucket_expression

        spec = parse_column(MODEL, "data.deadline|date", whitelist)
        with pytest.raises(ValidationError):
            date_bucket_expression(spec, "hour")

    def test_model_field_rejects_type_annotation(self, whitelist):
        with pytest.raises(ValidationError):
            parse_column(MODEL, "created_time|number", whitelist)

    def test_rejects_unknown_type(self, whitelist):
        with pytest.raises(ValidationError):
            parse_column(MODEL, "data.created|datetime", whitelist)

    def test_rejects_nested_path(self, whitelist):
        with pytest.raises(ValidationError):
            parse_column(MODEL, "data.a.b", whitelist)

    @pytest.mark.parametrize("raw", ["data.", ".kind", "data.a b", "data.键"])
    def test_rejects_invalid_segments(self, whitelist, raw):
        with pytest.raises(ValidationError):
            parse_column(MODEL, raw, whitelist)

    def test_rejects_non_json_root(self, whitelist):
        with pytest.raises(ValidationError):
            parse_column(MODEL, "schema_version.kind", whitelist)

    def test_rejects_root_not_in_whitelist(self, whitelist):
        with pytest.raises(ValidationError):
            parse_column(MODEL, "other.kind", whitelist)

    def test_rejects_empty(self, whitelist):
        with pytest.raises(ValidationError):
            parse_column(MODEL, "", whitelist)


class TestResolveColumns:
    def test_alias_conflict_detected(self, whitelist):
        # sanitize 后同名的两个键（a-b 与 a_b）必须在解析期拒绝，避免列串数据
        with pytest.raises(ValidationError):
            resolve_columns(MODEL, ["data.a-b", "data.a_b"], whitelist)

    def test_annotations_only_for_json(self, whitelist):
        specs = resolve_columns(MODEL, ["created_time", "data.kind"], whitelist)
        assert list(annotations_for(specs)) == ["json_data_kind"]

    def test_json_fields_of(self):
        assert "data" in json_fields_of(MODEL)
