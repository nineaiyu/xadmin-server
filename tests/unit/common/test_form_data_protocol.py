# -*- coding: utf-8 -*-
"""FormData 上传协议 v1 还原测试。

覆盖 AxiosMultiPartParser.format_data 的点分键展开规则：
嵌套 dict / 数组下标（含乱序补齐）/ 顶层 pks 批量 / 空值与混合形态。
协议权威文档：xadmin-docs `advanced/form-data-upload.md`。
"""
import pytest
from django.http import QueryDict

from common.drf.parsers.axios_form_data import format_data


@pytest.fixture
def query_dict():
    def _make(**items):
        qd = QueryDict("", mutable=True)
        for key, value in items.items():
            if isinstance(value, list):
                qd.setlist(key, value)
            else:
                qd[key] = value
        return qd

    return _make


def test_flat_key_kept(query_dict):
    data = query_dict(name="书", enabled="1")
    assert format_data(data) == {"name": "书", "enabled": "1"}


def test_nested_dict_shape(query_dict):
    data = query_dict(
        **{
            "category.value": "0",
            "admin.value": "1",
            "admin.label": "(isummer)",
            "admin.pk": "1",
        }
    )
    assert format_data(data) == {
        "category": {"value": "0"},
        "admin": {"value": "1", "label": "(isummer)", "pk": "1"},
    }


def test_array_indexes(query_dict):
    data = query_dict(
        **{
            "covers.0.value": "2",
            "covers.0.label": "1111",
            "covers.0.pk": "2",
            "covers.1.value": "11112",
            "covers.1.label": "11111111",
            "covers.1.pk": "11112",
        }
    )
    assert format_data(data) == {
        "covers": [
            {"value": "2", "label": "1111", "pk": "2"},
            {"value": "11112", "label": "11111111", "pk": "11112"},
        ]
    }


def test_array_out_of_order_fills_gaps(query_dict):
    """只出现下标 1 时，0 位由空 dict 补齐，保证索引位置不串位。"""
    data = query_dict(**{"covers.1.value": "x"})
    assert format_data(data) == {"covers": [{}, {"value": "x"}]}


def test_top_level_pks_takes_multivalue(query_dict):
    data = query_dict(pks=["1", "2", "3"])
    assert format_data(data) == {"pks": ["1", "2", "3"]}


def test_empty_input(query_dict):
    assert format_data(query_dict()) == {}


def test_mixed_flat_and_nested(query_dict):
    data = query_dict(name="x", **{"detail.is_active": "true"})
    assert format_data(data) == {"name": "x", "detail": {"is_active": "true"}}
