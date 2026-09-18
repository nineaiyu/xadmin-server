# -*- coding: utf-8 -*-
"""全局搜索 trigram 索引守护（system/search_indexes.py + 迁移快照）。

三类守护：
- **覆盖**：每个搜索提供者的检索字段必须落在「索引清单」或「豁免清单」（新增检索字段
  忘补索引/豁免时红灯——否则会静默退回顺序扫描，且只有上线后才可能被发现）；
- **漂移**：迁移快照 ↔ 运行期清单 ↔ 迁移 state_operations 三处索引名/表/字段一致；
- **降级与形态**：建索引 SQL 为 pg_trgm GIN 且幂等；非 PostgreSQL 不执行任何 DDL；
  扩展不可用时只告警、不再尝试建索引（不阻断迁移）。
"""

import importlib

from system import search_indexes
from system.search import SEARCH_PROVIDERS

MIGRATION = importlib.import_module("system.migrations.0010_search_trigram_indexes")


def _provider_table(provider) -> str:
    return provider.queryset().model._meta.db_table


class TestCoverage:
    def test_every_search_field_is_indexed_or_exempt(self):
        indexed = {(item.table, item.field) for item in search_indexes.SEARCH_TRGM_INDEXES}
        missing = []
        for provider in SEARCH_PROVIDERS:
            table = _provider_table(provider)
            for field in provider.text_fields:
                if (table, field) in indexed or (table, field) in search_indexes.SEARCH_TRGM_EXEMPT:
                    continue
                missing.append(f"{provider.key}: {table}.{field}")
        assert missing == [], f"以下检索字段既无 trigram 索引也无豁免登记：{missing}"

    def test_exempt_entries_carry_reason(self):
        empty = [key for key, reason in search_indexes.SEARCH_TRGM_EXEMPT.items() if not str(reason).strip()]
        assert empty == [], f"豁免登记必须写明理由：{empty}"

    def test_index_names_follow_convention(self):
        for item in search_indexes.SEARCH_TRGM_INDEXES:
            assert item.name.startswith("idx_"), item.name
            assert item.name.endswith("_trgm"), item.name


class TestMigrationDrift:
    def test_snapshot_matches_runtime_registry(self):
        snapshot = {name: (table, field) for name, table, field in MIGRATION.TRGM_INDEXES}
        registry = {item.name: (item.table, item.field) for item in search_indexes.SEARCH_TRGM_INDEXES}
        assert snapshot == registry, "迁移快照与 system/search_indexes.py 清单漂移（新增字段请补新迁移）"

    def test_state_operations_match_snapshot(self):
        operations = MIGRATION.Migration.operations
        state_ops = [op for op in operations if hasattr(op, "state_operations") and op.state_operations]
        assert len(state_ops) == 1, "迁移应仅有单个 SeparateDatabaseAndState（state 声明 + 受控执行）"
        declared = {op.index.name for op in state_ops[0].state_operations}
        assert declared == {name for name, _table, _field in MIGRATION.TRGM_INDEXES}


class TestSqlShape:
    def test_create_index_sql_is_idempotent_trigram(self):
        sql = search_indexes.create_index_sql("system_userinfo", "username", "idx_userinfo_username_trgm")
        assert sql == (
            "CREATE INDEX IF NOT EXISTS idx_userinfo_username_trgm ON system_userinfo USING gin (username gin_trgm_ops)"
        )
        assert search_indexes.drop_index_sql("idx_userinfo_username_trgm") == (
            "DROP INDEX IF EXISTS idx_userinfo_username_trgm"
        )


class _FakeSchemaEditor:
    def __init__(self, connection):
        self.connection = connection


class _RecordingPgConnection:
    """伪 PG 连接：记录执行的 SQL；fail_extension 时扩展创建抛错（权限不足场景）。"""

    vendor = "postgresql"

    def __init__(self, fail_extension: bool = False):
        self.log = []
        self.fail_extension = fail_extension

    def cursor(self):
        connection = self

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql):
                if connection.fail_extension and sql.startswith("CREATE EXTENSION"):
                    raise RuntimeError("permission denied to create extension")
                connection.log.append(sql)

        return _Cursor()


class _NonPgConnection:
    vendor = "mysql"

    def cursor(self):
        raise AssertionError("非 PostgreSQL 不应执行任何 DDL")


class TestDegradation:
    def test_migration_skips_non_postgres(self):
        MIGRATION._create_indexes(None, _FakeSchemaEditor(_NonPgConnection()))
        MIGRATION._drop_indexes(None, _FakeSchemaEditor(_NonPgConnection()))

    def test_migration_creates_extension_then_all_indexes(self):
        connection = _RecordingPgConnection()
        MIGRATION._create_indexes(None, _FakeSchemaEditor(connection))
        assert connection.log[0] == search_indexes.TRGM_EXTENSION_SQL
        created = connection.log[1:]
        assert len(created) == len(MIGRATION.TRGM_INDEXES)
        assert all("USING gin" in sql and sql.startswith("CREATE INDEX IF NOT EXISTS") for sql in created)

    def test_migration_degrades_when_extension_unavailable(self):
        connection = _RecordingPgConnection(fail_extension=True)
        MIGRATION._create_indexes(None, _FakeSchemaEditor(connection))
        assert connection.log == [], "扩展不可用时不应再尝试建索引（检索回退顺序扫描）"
