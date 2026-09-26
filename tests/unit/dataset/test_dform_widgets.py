# -*- coding: utf-8 -*-
"""动态表单控件校验：附件 / 日期范围 / 明细子表 / 金额 / 选人 / 级联。

守护四件事：
1. schema 侧：明细子表必须有列定义，列类型收敛、禁嵌套、列键唯一；级联选项树限层级与节点数；
2. 提交侧：附件为文件条目数组、日期范围有序、明细行按列校验且未知列拒绝；
   金额限小数位、选人限正整数主键、级联必须命中叶子路径；
3. 边界：文件数 / 行数 / 文本长度超限一律拒绝（防超深 JSON 入库）；
4. 平铺选项控件与树形/无选项控件互不串味（非法 options 一律拒绝）。
"""

import pytest
from django.core.exceptions import ValidationError

from dataset.utils.dform import (
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
    for field_type in ("upload", "daterange", "table", "amount", "user"):
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


CASCADER_OPTIONS = [
    {
        "value": "华东",
        "label": "华东",
        "children": [
            {"value": "上海", "label": "上海"},
            {"value": "江苏", "label": "江苏", "children": [{"value": "南京", "label": "南京"}]},
        ],
    },
    {"value": "华南", "label": "华南", "children": [{"value": "广东", "label": "广东"}]},
]


def test_amount_limits_precision_and_bounds():
    schema = schema_with({"key": "fee", "label": "金额", "type": "amount", "min": 0, "max": 100000, "precision": 2})
    validate_schema(schema)
    assert validate_submission_data(schema, {"fee": 12.34})["fee"] == 12.34
    assert validate_submission_data(schema, {"fee": 12})["fee"] == 12
    with pytest.raises(ValidationError):
        validate_submission_data(schema, {"fee": 12.345})
    with pytest.raises(ValidationError):
        validate_submission_data(schema, {"fee": -1})
    with pytest.raises(ValidationError):
        validate_submission_data(schema, {"fee": "12.3"})
    with pytest.raises(ValidationError):
        validate_schema(schema_with({"key": "fee", "label": "金额", "type": "amount", "precision": 9}))


def test_user_widget_accepts_pk_only():
    schema = schema_with({"key": "owner", "label": "证明人", "type": "user"})
    validate_schema(schema)
    assert validate_submission_data(schema, {"owner": 3})["owner"] == 3
    for bad in ("3", True, 0, -1, 1.5):
        with pytest.raises(ValidationError):
            validate_submission_data(schema, {"owner": bad})

    multi = schema_with({"key": "members", "label": "成员", "type": "user", "multiple": True})
    validate_schema(multi)
    assert validate_submission_data(multi, {"members": [1, 2]})["members"] == [1, 2]
    with pytest.raises(ValidationError):
        validate_submission_data(multi, {"members": 1})
    with pytest.raises(ValidationError):
        validate_submission_data(multi, {"members": [1, 0]})
    with pytest.raises(ValidationError):
        validate_schema(schema_with({"key": "owner", "label": "证明人", "type": "user", "multiple": "yes"}))


def test_cascader_requires_leaf_path():
    schema = schema_with({"key": "region", "label": "地区", "type": "cascader", "options": CASCADER_OPTIONS})
    validate_schema(schema)
    assert validate_submission_data(schema, {"region": ["华东", "江苏", "南京"]})["region"] == [
        "华东",
        "江苏",
        "南京",
    ]
    # 非叶子节点 / 路径不存在 / 非数组一律拒绝
    with pytest.raises(ValidationError):
        validate_submission_data(schema, {"region": ["华东", "江苏"]})
    with pytest.raises(ValidationError):
        validate_submission_data(schema, {"region": ["华东", "北京"]})
    with pytest.raises(ValidationError):
        validate_submission_data(schema, {"region": "华东"})
    # schema 侧：选项树缺 label / 空数组 / 超过三层一律拒绝
    with pytest.raises(ValidationError):
        validate_schema(
            schema_with({"key": "region", "label": "地区", "type": "cascader", "options": [{"value": "x"}]})
        )
    with pytest.raises(ValidationError):
        validate_schema(schema_with({"key": "region", "label": "地区", "type": "cascader", "options": []}))
    deep = {
        "value": "l1",
        "label": "L1",
        "children": [
            {
                "value": "l2",
                "label": "L2",
                "children": [{"value": "l3", "label": "L3", "children": [{"value": "l4", "label": "L4"}]}],
            }
        ],
    }
    with pytest.raises(ValidationError):
        validate_schema(schema_with({"key": "region", "label": "地区", "type": "cascader", "options": [deep]}))
