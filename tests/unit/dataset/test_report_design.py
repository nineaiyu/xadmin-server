# -*- coding: utf-8 -*-
"""报表设计（Report.design）规范化与校验单测（单一事实源）。"""

from types import SimpleNamespace

import pytest

from dataset.utils.report_design import (
    REPORT_MAX_COMPONENTS,
    ReportDesignError,
    design_components,
    design_export_columns,
    design_table_limit,
    normalize_report_design,
)

DATASET = SimpleNamespace(columns=["username", "gender", "is_active", "date_joined"])
NUMERIC = ["gender"]


def _normalise(raw):
    return normalize_report_design(raw, DATASET, NUMERIC)


def test_empty_design_means_legacy_behaviour():
    assert _normalise(None) == {}
    assert _normalise("") == {}
    assert _normalise({}) == {}


def test_columns_are_filtered_deduped_and_ordered():
    design = _normalise({"columns": ["gender", "username", "gender", "  "]})
    assert design["columns"] == ["gender", "username"]


def test_empty_columns_means_all():
    assert _normalise({"columns": [], "components": []})["columns"] == []


def test_unknown_column_rejected():
    with pytest.raises(ReportDesignError):
        _normalise({"columns": ["password"]})


def test_table_limit_default_and_bounds():
    assert _normalise({"components": []})["table_limit"] == 100
    assert _normalise({"table_limit": 10})["table_limit"] == 10
    assert _normalise({"table_limit": 500})["table_limit"] == 500
    for bad in (9, 501, 0, True, "100"):
        with pytest.raises(ReportDesignError):
            _normalise({"table_limit": bad})


def test_chart_component_normalised():
    design = _normalise(
        {
            "components": [
                {
                    "id": "c1",
                    "type": "bar",
                    "title": "  性别分布  ",
                    "group_by": "gender",
                    "metric": "count",
                    "span": 6,
                    "junk": "drop",
                }
            ]
        }
    )
    assert design["components"] == [
        {
            "id": "c1",
            "type": "bar",
            "span": 6,
            "metric": "count",
            "value_field": "",
            "title": "性别分布",
            "group_by": "gender",
        }
    ]


def test_number_component_defaults_and_no_group_by():
    design = _normalise({"components": [{"id": "n1", "type": "number", "metric": "sum", "value_field": "gender"}]})
    assert design["components"][0] == {
        "id": "n1",
        "type": "number",
        "span": 12,
        "metric": "sum",
        "value_field": "gender",
    }
    with pytest.raises(ReportDesignError):
        _normalise({"components": [{"id": "n1", "type": "number", "group_by": "gender"}]})


def test_line_component_keeps_date_trunc():
    design = _normalise(
        {
            "components": [
                {
                    "id": "l1",
                    "type": "line",
                    "group_by": "date_joined",
                    "date_trunc": "day",
                    "metric": "count",
                }
            ]
        }
    )
    assert design["components"][0]["date_trunc"] == "day"


class TestRejections:
    def test_unknown_component_type(self):
        with pytest.raises(ReportDesignError):
            _normalise({"components": [{"id": "x", "type": "table", "group_by": "gender"}]})

    def test_missing_component_id(self):
        with pytest.raises(ReportDesignError):
            _normalise({"components": [{"type": "bar", "group_by": "gender"}]})

    def test_duplicated_component_id(self):
        with pytest.raises(ReportDesignError):
            _normalise(
                {
                    "components": [
                        {"id": "same", "type": "bar", "group_by": "gender"},
                        {"id": "same", "type": "pie", "group_by": "gender"},
                    ]
                }
            )

    def test_chart_requires_group_by(self):
        with pytest.raises(ReportDesignError):
            _normalise({"components": [{"id": "c1", "type": "pie"}]})

    def test_unknown_group_by(self):
        with pytest.raises(ReportDesignError):
            _normalise({"components": [{"id": "c1", "type": "bar", "group_by": "missing"}]})

    def test_invalid_metric(self):
        with pytest.raises(ReportDesignError):
            _normalise({"components": [{"id": "c1", "type": "bar", "group_by": "gender", "metric": "median"}]})

    def test_sum_requires_numeric_value_field(self):
        with pytest.raises(ReportDesignError):
            _normalise({"components": [{"id": "c1", "type": "bar", "group_by": "gender", "metric": "sum"}]})
        with pytest.raises(ReportDesignError):
            _normalise(
                {
                    "components": [
                        {"id": "c1", "type": "bar", "group_by": "is_active", "metric": "sum", "value_field": "username"}
                    ]
                }
            )

    def test_unknown_value_field(self):
        with pytest.raises(ReportDesignError):
            _normalise({"components": [{"id": "c1", "type": "bar", "group_by": "gender", "value_field": "missing"}]})

    def test_invalid_span_and_date_trunc(self):
        with pytest.raises(ReportDesignError):
            _normalise({"components": [{"id": "c1", "type": "bar", "group_by": "gender", "span": 8}]})
        with pytest.raises(ReportDesignError):
            _normalise({"components": [{"id": "c1", "type": "line", "group_by": "gender", "date_trunc": "week"}]})

    def test_title_too_long(self):
        with pytest.raises(ReportDesignError):
            _normalise({"components": [{"id": "c1", "type": "number", "title": "标" * 65}]})

    def test_too_many_components(self):
        raw = {
            "components": [
                {"id": f"c{i}", "type": "number", "metric": "count"} for i in range(REPORT_MAX_COMPONENTS + 1)
            ]
        }
        with pytest.raises(ReportDesignError):
            _normalise(raw)

    def test_non_dict_payloads(self):
        with pytest.raises(ReportDesignError):
            _normalise([{"id": "c1"}])
        with pytest.raises(ReportDesignError):
            _normalise({"columns": "username"})
        with pytest.raises(ReportDesignError):
            _normalise({"components": "c1"})
        with pytest.raises(ReportDesignError):
            _normalise({"components": ["c1"]})


class TestReadHelpers:
    def test_export_columns_falls_back_to_dataset(self):
        assert design_export_columns({}, ["a", "b"]) == ["a", "b"]
        assert design_export_columns({"columns": []}, ["a", "b"]) == ["a", "b"]
        assert design_export_columns({"columns": ["b"]}, ["a", "b"]) == ["b"]
        assert design_export_columns(None, ["a"]) == ["a"]

    def test_export_columns_skips_removed_columns(self):
        """数据集列后续被删/改名时不阻断投递：逐列静默跳过。"""
        assert design_export_columns({"columns": ["b", "gone"]}, ["a", "b"]) == ["b"]

    def test_table_limit_reader_is_lenient(self):
        assert design_table_limit({}) == 100
        assert design_table_limit({"table_limit": 20}) == 20
        assert design_table_limit({"table_limit": 9999}) == 100
        assert design_table_limit({"table_limit": True}) == 100
        assert design_table_limit(None) == 100

    def test_design_components_reader_is_lenient(self):
        assert design_components({}) == []
        assert design_components({"components": "x"}) == []
        assert design_components({"components": [{"id": "c1"}]}) == []
        assert len(design_components({"components": [{"id": "c1", "type": "bar"}]})) == 1
