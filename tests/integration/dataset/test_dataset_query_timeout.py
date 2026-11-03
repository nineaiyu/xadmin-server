# -*- coding: utf-8 -*-
"""数据集执行超时守护：所有执行入口必须经过带超时的包装。

``execute_dataset`` / ``aggregate_dataset`` 是 HTTP execute/aggregate、WS
仪表盘/大屏推送（``execute_card``）、Celery 定时报表（``_render_workbook``）
与 NL 查数共用的唯一执行入口。历史上语句超时只在 HTTP 视图层设置，WS 与
Celery 路径执行同一批查询却无保护（慢查询可长期占用 worker / WS 线程）；
超时挂点下沉到执行函数本身后，任何调用方（含未来新增入口）自动获得保护。
本文件钉住四件事：

1. 两个执行入口都带 ``with_statement_timeout`` 包装标记（静态核对）；
2. 无 HTTP 请求上下文直调（模拟 WS / Celery）确实下发 ``SET LOCAL
   statement_timeout``，且取值来自 ``DATASET_QUERY_TIMEOUT_MS``；
3. WS 推送内核 ``execute_card`` 两条分支都路由到带包装的入口（调用链核对）；
4. 超时设置失败 fail-closed：异常上抛，不静默跑无超时查询。
"""

import uuid
from typing import Any

import pytest
from django.db import connection

from dataset.models import Dataset
from dataset.screen_data import KIND_AGGREGATE, KIND_EXECUTE, CardRef, execute_card
from dataset.utils.dataset import aggregate_dataset, execute_dataset
from dataset.utils.dataset_query import TIMEOUT_WRAPPED_ATTR
from system.models import ModelLabelField

pytestmark = pytest.mark.django_db

USER_BOUND = "identity.userinfo"


def _require_postgresql() -> None:
    """``statement_timeout`` 是 PostgreSQL 机制：其余后端（如 sqlite 测试档）跳过。"""
    if connection.vendor != "postgresql":
        pytest.skip("statement_timeout is PostgreSQL-only")


@pytest.fixture
def user_registry(db):
    root, _ = ModelLabelField.objects.get_or_create(
        name=USER_BOUND,
        defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "用户"},
    )
    ModelLabelField.objects.get_or_create(
        name="username",
        parent=root,
        defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "用户名"},
    )
    return root


@pytest.fixture
def username_dataset(user_registry, superuser):
    return Dataset.objects.create(
        name=f"超时守护数据集-{uuid.uuid4().hex[:6]}",
        bound_model=USER_BOUND,
        columns=["username"],
        filters=[],
        row_limit=100,
        visibility="shared",
        creator=superuser,
    )


@pytest.fixture
def executed_sql(monkeypatch):
    """记录经 ``CursorWrapper`` 下发的全部 (SQL, params)：SET LOCAL 与业务查询一并可见。"""
    from django.db.backends.utils import CursorWrapper

    executed: list[tuple[str, Any]] = []
    original = CursorWrapper.execute

    def spy(self, sql, params=None):
        executed.append((sql, params))
        return original(self, sql, params)

    monkeypatch.setattr(CursorWrapper, "execute", spy)
    return executed


def _timeout_params(executed: list[tuple[str, Any]]) -> list[Any]:
    return [params for sql, params in executed if "statement_timeout" in sql]


def test_execution_entries_wrapped():
    """静态核对：两个执行入口（含模块再导出面）必须带超时包装标记。"""
    from dataset.utils import dataset_query

    entries = (
        dataset_query.execute_dataset,
        dataset_query.aggregate_dataset,
        execute_dataset,
        aggregate_dataset,
    )
    for func in entries:
        assert getattr(func, TIMEOUT_WRAPPED_ATTR, False), (
            f"{func.__module__}.{func.__name__} 未挂 with_statement_timeout：WS / Celery 路径将失去语句超时保护"
        )


def test_execute_dataset_sets_timeout_without_request_context(settings, superuser, username_dataset, executed_sql):
    """直调执行入口（模拟 Celery / 无请求线程）：必须下发 SET LOCAL 且取配置值。"""
    _require_postgresql()
    settings.DATASET_QUERY_TIMEOUT_MS = 1234
    execute_dataset(username_dataset, superuser, count_only=True)
    assert _timeout_params(executed_sql) == [[1234]]


def test_aggregate_dataset_sets_timeout_without_request_context(settings, superuser, username_dataset, executed_sql):
    """直调聚合入口（模拟 Celery / 无请求线程）：必须下发 SET LOCAL 且取配置值。"""
    _require_postgresql()
    settings.DATASET_QUERY_TIMEOUT_MS = 2345
    aggregate_dataset(username_dataset, superuser, group_by="username")
    assert _timeout_params(executed_sql) == [[2345]]


def test_execute_card_number_routes_through_timeout(settings, superuser, username_dataset, executed_sql):
    """WS 推送内核 execute_card 数字卡分支（execute_dataset，count_only）。"""
    _require_postgresql()
    settings.DATASET_QUERY_TIMEOUT_MS = 3456
    ref = CardRef(
        card="card-number",
        dashboard="",
        dataset=str(username_dataset.pk),
        chart_type="number",
        kind=KIND_EXECUTE,
        group_by="",
        metric="count",
        date_trunc="",
        value_field="",
    )
    execute_card(ref, superuser, username_dataset)
    assert _timeout_params(executed_sql) == [[3456]]


def test_execute_card_chart_routes_through_timeout(settings, superuser, username_dataset, executed_sql):
    """WS 推送内核 execute_card 图表卡分支（aggregate_dataset）。"""
    _require_postgresql()
    settings.DATASET_QUERY_TIMEOUT_MS = 4567
    ref = CardRef(
        card="card-chart",
        dashboard="",
        dataset=str(username_dataset.pk),
        chart_type="pie",
        kind=KIND_AGGREGATE,
        group_by="username",
        metric="count",
        date_trunc="",
        value_field="",
    )
    execute_card(ref, superuser, username_dataset)
    assert _timeout_params(executed_sql) == [[4567]]


def test_timeout_setting_failure_is_fail_closed(settings, superuser, username_dataset, monkeypatch):
    """SET LOCAL 失败必须上抛：宁可报错也不静默跑无超时查询（保护优先于可用性）。"""
    _require_postgresql()
    from django.db.backends.utils import CursorWrapper

    original = CursorWrapper.execute

    def boom(self, sql, params=None):
        if "statement_timeout" in sql:
            raise RuntimeError("set statement_timeout failed")
        return original(self, sql, params)

    monkeypatch.setattr(CursorWrapper, "execute", boom)
    with pytest.raises(RuntimeError):
        execute_dataset(username_dataset, superuser, count_only=True)
