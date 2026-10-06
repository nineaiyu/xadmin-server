# -*- coding: utf-8 -*-
"""大屏序列化器布局校验（ScreenSerializer.validate_layout）回归测试。

锁定指标卡窗格引用数据集的校验语义与查询行为：
- 引用未知数据集（含非法主键形态）一律以既有文案拒绝（窗格 id 定位）；
- sum/avg 取值列必须落在数据集数值列白名单内（与报表组件同口径）；
- 校验只加载被引用的数据集：不含指标卡的布局不查询数据集表。
"""

import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import serializers

from dataset.models.dataset import Dashboard, Dataset
from dataset.serializers.analysis import ScreenSerializer

pytestmark = pytest.mark.django_db


@pytest.fixture
def dataset(db):
    return Dataset.objects.create(
        name="布局校验数据集",
        bound_model="identity.userinfo",
        columns=["username", "gender"],
        visibility="shared",
    )


def _pane(pk, pane_type, **extra):
    pane = {"pk": pk, "type": pane_type, "x": 0, "y": 0, "w": 3, "h": 2}
    pane.update(extra)
    return pane


def _metric_pane(dataset_pk, **extra):
    return [_pane("m1", "metric", dataset=dataset_pk, metric="sum", **extra)]


def test_metric_sum_with_numeric_value_field_passes(dataset):
    layout = ScreenSerializer().validate_layout(_metric_pane(str(dataset.pk), value_field="gender"))
    assert layout[0]["dataset"] == str(dataset.pk)
    assert layout[0]["value_field"] == "gender"


def test_metric_sum_with_non_numeric_value_field_rejected(dataset):
    with pytest.raises(serializers.ValidationError) as excinfo:
        ScreenSerializer().validate_layout(_metric_pane(str(dataset.pk), value_field="username"))
    assert "m1" in str(excinfo.value)


def test_metric_with_unknown_dataset_rejected(dataset):
    """引用的数据集不存在仍须拒绝：known 集为空时存在性校验会被结构校验跳过，依赖兜底语义。"""
    with pytest.raises(serializers.ValidationError) as excinfo:
        ScreenSerializer().validate_layout(_metric_pane(str(uuid.uuid4())))
    assert "m1" in str(excinfo.value)


def test_metric_with_malformed_dataset_reference_rejected(dataset):
    with pytest.raises(serializers.ValidationError):
        ScreenSerializer().validate_layout(_metric_pane("not-a-uuid"))


def test_layout_without_metric_panes_skips_dataset_queries(dataset):
    dashboard = Dashboard.objects.create(name="布局看板", layout=[], visibility="shared")
    serializer = ScreenSerializer()
    with CaptureQueriesContext(connection) as ctx:
        serializer.validate_layout([_pane("d1", "dashboard", dashboard=str(dashboard.pk))])
    assert not [q["sql"] for q in ctx.captured_queries if "dataset_dataset" in q["sql"]]
