# -*- coding: utf-8 -*-
"""数据集执行的外部行数上限（``execute_dataset`` 的 ``max_rows``）回归测试。

明细消费方（如定时报表渲染）只需要前 N 行：行数上限与数据集 row_limit 取小者
下推到 SQL，避免全量物化后在 Python 侧切片；total 仍按未截断的完整行集统计
（COUNT 口径不变）；数据集未声明排序且绑定模型无默认排序时按主键稳定取行，
LIMIT 结果可复现，数据集显式声明的排序优先。
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from dataset.models.dataset import Dataset
from dataset.utils.dataset import execute_dataset
from identity.models import UserInfo
from system.models import ModelLabelField, Monitor

pytestmark = pytest.mark.django_db


@pytest.fixture
def model_registry(db):
    root, _ = ModelLabelField.objects.get_or_create(
        name="identity.userinfo",
        defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "用户"},
    )
    for name in ("username", "nickname", "created_time"):
        ModelLabelField.objects.get_or_create(
            name=name, parent=root, defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": name}
        )
    return root


@pytest.fixture
def dataset(model_registry, superuser):
    return Dataset.objects.create(
        name=f"行数上限数据集-{superuser.pk}",
        bound_model="identity.userinfo",
        columns=["username", "nickname", "created_time"],
        row_limit=1000,
        visibility="shared",
        creator=superuser,
    )


def _seed_users(count=6):
    return [
        UserInfo.objects.create_user(username=f"max_rows_user_{index:02d}", password="Test@123456")
        for index in range(count)
    ]


def test_max_rows_caps_rows_and_keeps_total_count(dataset, superuser):
    _seed_users()
    result = execute_dataset(dataset, superuser, max_rows=4)
    assert len(result["rows"]) == 4
    # total 不随 max_rows 截断：仍统计完整行集
    assert result["total"] == UserInfo.objects.count()
    # 绑定模型自带默认排序（userinfo 按 -date_joined）→ 行序与既有全量物化口径一致
    expected = list(UserInfo.objects.all().values_list("username", flat=True)[:4])
    assert [row["username"] for row in result["rows"]] == expected


def test_max_rows_pushes_limit_into_sql(dataset, superuser):
    _seed_users()
    with CaptureQueriesContext(connection) as ctx:
        result = execute_dataset(dataset, superuser, max_rows=4)
    assert len(result["rows"]) == 4
    row_queries = [
        q["sql"] for q in ctx.captured_queries if "COUNT(" not in q["sql"].upper() and '"identity_userinfo"' in q["sql"]
    ]
    assert any("LIMIT 4" in sql for sql in row_queries)


def test_max_rows_never_exceeds_dataset_row_limit(dataset, superuser):
    _seed_users()
    dataset.row_limit = 3
    dataset.save(update_fields=["row_limit"])
    assert len(execute_dataset(dataset, superuser, max_rows=4)["rows"]) == 3


def test_declared_dataset_ordering_wins_over_pk_fallback(dataset, superuser):
    _seed_users()
    dataset.ordering = "-username"
    dataset.save(update_fields=["ordering"])
    result = execute_dataset(dataset, superuser, max_rows=4)
    expected = list(UserInfo.objects.order_by("-username").values_list("username", flat=True)[:4])
    assert [row["username"] for row in result["rows"]] == expected


def test_pk_fallback_when_neither_dataset_nor_model_declares_ordering(superuser, db):
    """数据集与绑定模型（monitor 无默认排序）都未声明排序时按主键稳定取行。"""
    root, _ = ModelLabelField.objects.get_or_create(
        name="system.monitor", defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "监控"}
    )
    for name in ("cpu_load", "memory_used"):
        ModelLabelField.objects.get_or_create(
            name=name, parent=root, defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": name}
        )
    dataset = Dataset.objects.create(
        name=f"监控数据集-{superuser.pk}",
        bound_model="system.monitor",
        columns=["cpu_load", "memory_used"],
        row_limit=1000,
        visibility="shared",
        creator=superuser,
    )
    for index in range(6):
        Monitor.objects.create(cpu_load=float(index), memory_used=float(index))
    result = execute_dataset(dataset, superuser, max_rows=2)
    assert len(result["rows"]) == 2
    expected = list(Monitor.objects.order_by("pk").values_list("cpu_load", flat=True)[:2])
    assert [row["cpu_load"] for row in result["rows"]] == expected
