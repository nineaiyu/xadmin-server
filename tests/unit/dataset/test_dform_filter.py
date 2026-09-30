# -*- coding: utf-8 -*-
"""可筛选字段物化与筛选编译（纯函数面）：物化取值、fail-closed 校验、数组包含语义。"""

import pytest
from django.core.exceptions import ValidationError

from dataset.utils.dform_filter import (
    build_filter_contains,
    build_filter_data,
    compile_materialized_filters,
    filterable_items,
    filterable_keys,
)

SCHEMA = {
    "fields": [
        {"key": "name", "label": "姓名", "type": "input", "filterable": True},
        # 未勾选 → 不物化、不可筛选
        {"key": "remark", "label": "备注", "type": "textarea"},
        {"key": "score", "label": "得分", "type": "number", "filterable": True},
        {"key": "level", "label": "级别", "type": "select", "options": ["P4", "P5"], "filterable": True},
        {"key": "skills", "label": "技能", "type": "checkbox", "options": ["a", "b"], "filterable": True},
        {"key": "archived", "label": "归档", "type": "switch", "filterable": True},
        {"key": "owner", "label": "负责人", "type": "user", "filterable": True},
        # 类型不可物化 → 即使误勾选也不进筛选面
        {"key": "files", "label": "附件", "type": "upload", "filterable": True},
    ]
}


def test_filterable_items_and_keys():
    items = filterable_items(SCHEMA)
    assert [item["key"] for item in items] == ["name", "score", "level", "skills", "archived", "owner"]
    assert filterable_keys(SCHEMA) == ["name", "score", "level", "skills", "archived", "owner"]


def test_build_filter_data_skips_empty_and_unmarked():
    data = {
        "name": "张三",
        "remark": "不应物化",
        "score": 0,  # 0 是有效取值（非空）
        "level": None,
        "skills": [],
        "archived": False,  # False 是有效取值
        "owner": 7,
        "files": [],
    }
    assert build_filter_data(SCHEMA, data) == {"name": "张三", "score": 0, "archived": False, "owner": 7}


def test_build_filter_contains_rejects_unfilterable_key():
    with pytest.raises(ValidationError) as exc:
        build_filter_contains(SCHEMA, {"remark": "x"})
    assert "remark" in str(exc.value)
    with pytest.raises(ValidationError):
        build_filter_contains(SCHEMA, {"unknown_key": "x"})


def test_build_filter_contains_coerces_by_type():
    assert build_filter_contains(SCHEMA, {"score": "88"}) == {"score": 88}
    assert build_filter_contains(SCHEMA, {"score": 88.5}) == {"score": 88.5}
    assert build_filter_contains(SCHEMA, {"archived": "false"}) == {"archived": False}
    assert build_filter_contains(SCHEMA, {"owner": "7"}) == {"owner": 7}
    assert build_filter_contains(SCHEMA, {"name": "张三"}) == {"name": "张三"}
    # 数组型字段：标量包成单元素数组（JSON 数组包含语义 = 至少命中一项）
    assert build_filter_contains(SCHEMA, {"skills": "a"}) == {"skills": ["a"]}
    assert build_filter_contains(SCHEMA, {"skills": ["a", "b"]}) == {"skills": ["a", "b"]}
    # 空值条件跳过（等价于不筛）
    assert build_filter_contains(SCHEMA, {"name": "", "score": None}) == {}


def test_build_filter_contains_rejects_bad_value_shape():
    with pytest.raises(ValidationError):
        build_filter_contains(SCHEMA, {"score": "abc"})
    with pytest.raises(ValidationError):
        build_filter_contains(SCHEMA, {"archived": "maybe"})


def test_compile_materialized_filters_generic_mode():
    """无 schema 上下文（不限表单的列表）：通用形态校验，非法键/对象拒绝。"""
    assert compile_materialized_filters('{"name": "张三", "score": 88}') == {"name": "张三", "score": 88}
    assert compile_materialized_filters("") == {}
    assert compile_materialized_filters("{}") == {}
    with pytest.raises(ValidationError):
        compile_materialized_filters('{"Bad-Key": 1}')  # 非法字段 key
    with pytest.raises(ValidationError):
        compile_materialized_filters('{"name": {"nested": 1}}')  # 嵌套对象不是筛选取值
    with pytest.raises(ValidationError):
        compile_materialized_filters("{not-json}")


def test_compile_materialized_filters_strict_mode():
    """给出 schema：字段必须在可筛选面内（fail-closed）。"""
    assert compile_materialized_filters('{"level": "P5"}', SCHEMA) == {"level": "P5"}
    with pytest.raises(ValidationError):
        compile_materialized_filters('{"remark": "x"}', SCHEMA)


def test_apply_materialized_contains_backend_branches(monkeypatch):
    """PostgreSQL 走 JSON 包含查询（GIN 面）；其它后端退化逐键精确比较；空条件不筛。"""
    from dataset.utils import dform_filter

    class _FakeQuerySet:
        def __init__(self):
            self.calls = []

        def filter(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return self

    qs = _FakeQuerySet()
    monkeypatch.setattr(dform_filter, "_json_contains_supported", lambda: True)
    dform_filter.apply_materialized_contains(qs, {"level": "P5"})
    assert qs.calls == [((), {"filter_data__contains": {"level": "P5"}})]

    fallback = _FakeQuerySet()
    monkeypatch.setattr(dform_filter, "_json_contains_supported", lambda: False)
    dform_filter.apply_materialized_contains(fallback, {"level": "P5"})
    (args, kwargs) = fallback.calls[0]
    assert kwargs == {}
    assert len(args) == 1 and args[0].children == [("filter_data__level", "P5")]

    assert dform_filter.apply_materialized_contains(fallback, {}) is fallback
