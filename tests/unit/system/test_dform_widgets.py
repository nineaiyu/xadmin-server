# -*- coding: utf-8 -*-
"""动态表单控件校验：附件 / 日期范围 / 明细子表。

守护三件事：
1. schema 侧：明细子表必须有列定义，列类型收敛、禁嵌套、列键唯一；
2. 提交侧：附件为文件条目数组、日期范围有序、明细行按列校验且未知列拒绝；
3. 边界：文件数 / 行数 / 文本长度超限一律拒绝（防超深 JSON 入库）。
"""

import pytest
from django.core.exceptions import ValidationError

from system.utils.dform import (
    MAX_TABLE_ROWS,
    MAX_UPLOAD_FILES,
    validate_schema,
    validate_submission_data,
)


def schema_with(field):
    return {"fields": [field]}


def test_table_requires_columns():
    with pytest.raises(ValidationError):
        validate_schema(schema_with({"key": "items", "label": "明细", "type": "table"}))


def test_table_rejects_nested_table_column():
    with pytest.raises(ValidationError):
        validate_schema(
            schema_with(
                {
                    "key": "items",
                    "label": "明细",
                    "type": "table",
                    "columns": [{"key": "sub", "label": "子表", "type": "table"}],
                }
            )
        )


def test_table_rejects_duplicate_or_invalid_column_keys():
    with pytest.raises(ValidationError):
        validate_schema(
            schema_with(
                {
                    "key": "items",
                    "label": "明细",
                    "type": "table",
                    "columns": [
                        {"key": "a", "label": "A", "type": "input"},
                        {"key": "a", "label": "重复", "type": "input"},
                    ],
                }
            )
        )
    with pytest.raises(ValidationError):
        validate_schema(
            schema_with(
                {
                    "key": "items",
                    "label": "明细",
                    "type": "table",
                    "columns": [{"key": "Bad Key", "label": "非法", "type": "input"}],
                }
            )
        )


def test_table_select_column_requires_options():
    with pytest.raises(ValidationError):
        validate_schema(
            schema_with(
                {
                    "key": "items",
                    "label": "明细",
                    "type": "table",
                    "columns": [{"key": "degree", "label": "学历", "type": "select"}],
                }
            )
        )


def test_upload_accepts_file_entries_and_rejects_bad_shape():
    schema = schema_with({"key": "files", "label": "附件", "type": "upload"})
    validate_schema(schema)
    data = validate_submission_data(schema, {"files": [{"pk": "f1", "filename": "a.pdf"}]})
    assert data["files"] == [{"pk": "f1", "filename": "a.pdf"}]
    with pytest.raises(ValidationError):
        validate_submission_data(schema, {"files": ["f1"]})
    with pytest.raises(ValidationError):
        validate_submission_data(schema, {"files": [{"filename": "缺 pk"}]})
    with pytest.raises(ValidationError):
        validate_submission_data(schema, {"files": [{"pk": str(i)} for i in range(MAX_UPLOAD_FILES + 1)]})


def test_daterange_requires_ordered_pair():
    schema = schema_with({"key": "range", "label": "日期范围", "type": "daterange"})
    validate_schema(schema)
    data = validate_submission_data(schema, {"range": ["2026-01-01", "2026-12-31"]})
    assert data["range"] == ["2026-01-01", "2026-12-31"]
    with pytest.raises(ValidationError):
        validate_submission_data(schema, {"range": ["2026-12-31", "2026-01-01"]})
    with pytest.raises(ValidationError):
        validate_submission_data(schema, {"range": ["2026-01-01"]})
    with pytest.raises(ValidationError):
        validate_submission_data(schema, {"range": ["20260101", "2026-01-02"]})


def test_table_rows_normalized_and_bounded():
    schema = schema_with(
        {
            "key": "items",
            "label": "明细",
            "type": "table",
            "columns": [
                {"key": "name", "label": "名称", "type": "input"},
                {"key": "count", "label": "数量", "type": "number"},
                {"key": "degree", "label": "学历", "type": "select", "options": ["本科", "硕士"]},
            ],
        }
    )
    validate_schema(schema)
    data = validate_submission_data(
        schema,
        {"items": [{"name": "电脑", "count": 2, "degree": "硕士"}, {"name": "显示器"}]},
    )
    assert data["items"] == [
        {"name": "电脑", "count": 2, "degree": "硕士"},
        {"name": "显示器", "count": None, "degree": None},
    ]
    with pytest.raises(ValidationError):
        validate_submission_data(schema, {"items": [{"name": "x", "unknown": 1}]})
    with pytest.raises(ValidationError):
        validate_submission_data(schema, {"items": [{"name": "x"} for _ in range(MAX_TABLE_ROWS + 1)]})
    with pytest.raises(ValidationError):
        validate_submission_data(schema, {"items": [{"name": "x", "degree": "博士"}]})
    with pytest.raises(ValidationError):
        validate_submission_data(schema, {"items": "not a list"})


def test_new_widgets_do_not_accept_options():
    for field_type in ("upload", "daterange", "table"):
        with pytest.raises(ValidationError):
            validate_schema(
                schema_with(
                    {
                        "key": "widget",
                        "label": "控件",
                        "type": field_type,
                        "options": ["a"],
                        "columns": [{"key": "c", "label": "C", "type": "input"}],
                    }
                )
            )
