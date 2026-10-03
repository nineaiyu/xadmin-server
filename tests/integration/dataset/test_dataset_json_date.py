# -*- coding: utf-8 -*-
"""数据集 JSON 日期列与趋势分桶集成测试。

覆盖：month/day 桶（Substr 前缀，桶名与模型字段路径同格式）、date_trunc 需要
``|date`` 标注（fail-closed）、日期区间过滤（ISO 文本序）、``config.date_field``
的 JSON 日期形态校验、表单 date 字段的 ISO 值契约（写入侧收紧）。
"""

import pytest
from django.core.exceptions import ValidationError

from dataset.models import Dataset
from dataset.models.dform import DynamicForm, DynamicFormSubmission
from dataset.utils.dataset import aggregate_dataset, execute_dataset, validate_dataset
from system.models import ModelLabelField

pytestmark = pytest.mark.django_db

BOUND = "dataset.dynamicformsubmission"
FORMS_URL = "/api/dataset/dynamic-forms"
SUBMISSIONS_URL = "/api/dataset/dynamic-form-submissions"


@pytest.fixture
def registry(db):
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
    return root


@pytest.fixture
def submissions(registry, superuser):
    form = DynamicForm.objects.create(
        name="E2E-JSON日期",
        schema={
            "fields": [
                {"key": "deadline", "label": "截止日", "type": "date"},
                {"key": "kind", "label": "类别", "type": "select"},
            ]
        },
        is_active=True,
        creator=superuser,
    )
    for deadline, kind in [
        ("2026-09-01", "a"),
        ("2026-09-15", "b"),
        ("2026-10-02", "a"),
        ("2026-10-20", "a"),
    ]:
        DynamicFormSubmission.objects.create(form=form, data={"deadline": deadline, "kind": kind}, creator=superuser)
    return form


@pytest.fixture
def dataset(registry, superuser):
    return Dataset.objects.create(
        name="JSON日期数据集",
        bound_model=BOUND,
        columns=["data.deadline|date", "data.kind"],
        filters=[],
        row_limit=100,
        visibility="shared",
        creator=superuser,
    )


class TestJsonDateTrend:
    def test_month_buckets(self, dataset, submissions, superuser):
        result = aggregate_dataset(
            dataset, superuser, group_by="data.deadline|date", metric="count", date_trunc="month"
        )
        assert sorted((item["name"], item["value"]) for item in result["series"]) == [
            ("2026-09", 2),
            ("2026-10", 2),
        ]

    def test_day_buckets(self, dataset, submissions, superuser):
        result = aggregate_dataset(dataset, superuser, group_by="data.deadline|date", metric="count", date_trunc="day")
        assert [item["name"] for item in result["series"]] == [
            "2026-09-01",
            "2026-09-15",
            "2026-10-02",
            "2026-10-20",
        ]

    def test_trend_requires_date_marker(self, dataset, submissions, superuser):
        """无 |date 标注的 JSON 列做趋势：fail-closed（分桶语义要求 ISO 值契约）。"""
        with pytest.raises(ValidationError):
            aggregate_dataset(dataset, superuser, group_by="data.kind", metric="count", date_trunc="day")

    def test_group_without_trend_still_works(self, dataset, submissions, superuser):
        """不趋势时 date 列与其它 JSON 列一致（文本分组）。"""
        result = aggregate_dataset(dataset, superuser, group_by="data.deadline|date")
        assert len(result["series"]) == 4


class TestJsonDateFilter:
    def test_range_filter_uses_iso_text_order(self, dataset, submissions, superuser):
        dataset.filters = [{"field": "data.deadline|date", "op": "gte", "value": "2026-09-15"}]
        result = execute_dataset(dataset, superuser)
        assert sorted(row["data.deadline|date"] for row in result["rows"]) == [
            "2026-09-15",
            "2026-10-02",
            "2026-10-20",
        ]

    def test_lte_filter(self, dataset, submissions, superuser):
        dataset.filters = [{"field": "data.deadline|date", "op": "lte", "value": "2026-09-15"}]
        result = execute_dataset(dataset, superuser)
        assert sorted(row["data.deadline|date"] for row in result["rows"]) == ["2026-09-01", "2026-09-15"]


class TestTrendConfig:
    def test_config_accepts_json_date_column(self, registry, superuser):
        instance = Dataset(
            bound_model=BOUND,
            columns=["data.deadline|date"],
            config={"date_field": "data.deadline|date"},
            row_limit=100,
        )
        validate_dataset(instance)  # 不抛即通过

    def test_config_rejects_unmarked_json(self, registry, superuser):
        instance = Dataset(
            bound_model=BOUND,
            columns=["data.deadline"],
            config={"date_field": "data.deadline"},
            row_limit=100,
        )
        with pytest.raises(ValidationError):
            validate_dataset(instance)

    def test_config_rejects_column_outside_columns(self, registry, superuser):
        instance = Dataset(
            bound_model=BOUND,
            columns=["data.kind"],
            config={"date_field": "data.deadline|date"},
            row_limit=100,
        )
        with pytest.raises(ValidationError):
            validate_dataset(instance)


class TestDateValueContract:
    def _form(self, auth_client):
        return auth_client.post(
            FORMS_URL,
            {
                "name": "E2E-日期契约",
                "is_active": True,
                "schema": {"fields": [{"key": "deadline", "label": "截止日", "type": "date"}]},
            },
            format="json",
        ).json()["data"]

    def test_rejects_non_iso_date(self, auth_client):
        form = self._form(auth_client)
        resp = auth_client.post(
            SUBMISSIONS_URL, {"form": form["pk"], "data": {"deadline": "2026/09/01"}}, format="json"
        )
        assert resp.status_code == 400

    def test_accepts_iso_date(self, auth_client):
        form = self._form(auth_client)
        resp = auth_client.post(
            SUBMISSIONS_URL, {"form": form["pk"], "data": {"deadline": "2026-09-01"}}, format="json"
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == 1000
