# -*- coding: utf-8 -*-
"""表单 schema 版本历史（ADR-070）：按版本解析 / 历史字段合并 / 未知键兜底。"""

import pytest

from dataset.models.dform import DynamicForm, DynamicFormSubmission
from dataset.utils.dform_history import (
    HISTORICAL_MARK,
    merged_fields,
    merged_fields_of_forms,
    schema_for_version,
    submission_schema,
)

pytestmark = pytest.mark.django_db


def _field(key, label):
    return {"key": key, "label": label, "type": "input"}


@pytest.fixture
def form(db):
    """v2 表单：v1 快照 = [a, b]；当前 = [a, c]（b 已删除）。"""
    return DynamicForm.objects.create(
        name="历史字段表单",
        is_active=True,
        schema={"fields": [_field("a", "甲"), _field("c", "丙")]},
        schema_version=2,
        schema_history=[
            {
                "version": 1,
                "schema": {"fields": [_field("a", "甲"), _field("b", "乙")]},
                "updated_by": "admin",
            }
        ],
    )


class TestSchemaForVersion:
    def test_snapshot_hit(self, form):
        schema = schema_for_version(form, 1)
        assert [item["key"] for item in schema["fields"]] == ["a", "b"]

    def test_current_version(self, form):
        schema = schema_for_version(form, 2)
        assert [item["key"] for item in schema["fields"]] == ["a", "c"]

    def test_missing_version_falls_back_to_current(self, form):
        schema = schema_for_version(form, 99)
        assert [item["key"] for item in schema["fields"]] == ["a", "c"]


class TestMergedFields:
    def test_current_first_then_historical(self, form):
        fields = merged_fields(form)
        assert [item["key"] for item in fields] == ["a", "c", "b"]
        historical = next(item for item in fields if item["key"] == "b")
        assert historical["historical"] is True
        assert historical["label"] == f"乙{HISTORICAL_MARK}"

    def test_current_field_keeps_original_mark(self, form):
        fields = merged_fields(form)
        assert all(not item.get("historical") for item in fields if item["key"] != "b")

    def test_without_history_keeps_current(self, form):
        form.schema_history = []
        assert [item["key"] for item in merged_fields(form)] == ["a", "c"]

    def test_cross_form_dedup_and_order(self, form):
        other = DynamicForm.objects.create(
            name="其他表单",
            schema={"fields": [_field("a", "甲"), _field("d", "丁")]},
        )
        keys = [item["key"] for item in merged_fields_of_forms([form, other])]
        assert keys == ["a", "c", "b", "d"]


class TestSubmissionSchema:
    def test_snapshot_render_with_historical_mark(self, form):
        obj = DynamicFormSubmission.objects.create(form=form, data={"a": "A1", "b": "B1"}, schema_version=1)
        fields = submission_schema(obj)
        assert [item["key"] for item in fields] == ["a", "b"]
        assert next(item for item in fields if item["key"] == "b")["historical"] is True
        assert not next(item for item in fields if item["key"] == "a").get("historical")

    def test_unknown_keys_are_exposed(self, form):
        """data 里不属于任何已知 schema 的键：以 key 为标签兜底（值可见）。"""
        obj = DynamicFormSubmission.objects.create(form=form, data={"a": "x", "zzz": "旧值"}, schema_version=99)
        fields = submission_schema(obj)
        fallback = next(item for item in fields if item["key"] == "zzz")
        assert fallback["historical"] is True
        assert fallback["label"] == f"zzz{HISTORICAL_MARK}"

    def test_snapshot_missing_still_reveals_data(self, form):
        """快照超出保留窗口（历史被裁）：当前 schema 为底 + data 其余键仍可见。"""
        form.schema_history = []
        form.save(update_fields=["schema_history"])
        obj = DynamicFormSubmission.objects.create(form=form, data={"a": "x", "gone": "旧值"}, schema_version=1)
        assert [item["key"] for item in submission_schema(obj)] == ["a", "c", "gone"]
