# -*- coding: utf-8 -*-
"""物化筛选列 GIN 索引守护（索引本体 + 迁移快照 + 查询形态绑定）。

``filter_data`` 是表单提交「可筛选字段」的物化列（真实值在 ``data``），列表筛选编译为
单条 JSON 包含查询（``filter_data @> {...}``）。实测口径（2026-10，真实 PG17）：
20 万行单键包含 GIN 命中 8.5ms、200 万行双键包含 8.9ms；同表 JSON 键提取（``data ->> key``）
无索引路径 20 万→200 万行呈近线性增长（24ms→152ms；分组聚合 74ms→415ms 且排序落盘）。
索引被误删或查询形态被改写为路径匹配（``filter_data__<key>=``）会让筛选链路静默退化为
顺序扫描——本文件做三类守护：

- **索引本体**：运行期模型 Meta 的 GinIndex 在列且名字/字段锁定（清理"看起来孤立"的索引时红灯）；
- **迁移快照**：初始迁移（清库重建链路的生产形态）携带同一索引，模型 Meta 与迁移双向锁步；
- **查询形态绑定**：PG 分支生成的 SQL 使用 ``@>``（JSON 包含，可命中 GIN）。
"""

import importlib

from django.contrib.postgres.indexes import GinIndex
from django.db.models import JSONField

from dataset.models.dform import DynamicFormSubmission

GIN_INDEX_NAME = "idx_dformsub_filter_gin"
MODEL_NAME = "DynamicFormSubmission"


def _gin_indexes_of_model() -> list[tuple[str, list[str]]]:
    return [
        (index.name, list(index.fields)) for index in DynamicFormSubmission._meta.indexes if isinstance(index, GinIndex)
    ]


def _gin_indexes_in_initial_migration() -> list[tuple[str, list[str]]]:
    module = importlib.import_module("dataset.migrations.0001_initial")
    found: list[tuple[str, list[str]]] = []
    for op in module.Migration.operations:
        if getattr(op, "name", None) != MODEL_NAME:
            continue
        for index in (getattr(op, "options", None) or {}).get("indexes") or []:
            if isinstance(index, GinIndex):
                found.append((index.name, list(index.fields)))
    return found


class TestIndexBody:
    def test_filter_data_is_json_field(self):
        assert isinstance(DynamicFormSubmission._meta.get_field("filter_data"), JSONField)

    def test_gin_index_registered_on_meta(self):
        assert _gin_indexes_of_model() == [(GIN_INDEX_NAME, ["filter_data"])]


class TestMigrationSnapshot:
    def test_initial_migration_carries_gin_index(self):
        """清库重建链路（全新 0001）必须携带同一索引——模型 Meta 与迁移双向锁步。"""
        assert _gin_indexes_in_initial_migration() == [(GIN_INDEX_NAME, ["filter_data"])]


class TestQueryShapeBinding:
    def test_compiled_sql_uses_jsonb_contains_operator(self, db):
        """PG 分支编译产物必须是 ``@>``（JSON 包含）——路径匹配形态会让 GIN 失效。"""
        from dataset.utils.dform_filter import apply_materialized_contains

        queryset = apply_materialized_contains(DynamicFormSubmission.objects.all(), {"level": "P5"})
        assert "@>" in str(queryset.query)

    def test_empty_conditions_do_not_touch_queryset(self):
        from dataset.utils.dform_filter import apply_materialized_contains

        queryset = DynamicFormSubmission.objects.all()
        assert apply_materialized_contains(queryset, {}) is queryset
