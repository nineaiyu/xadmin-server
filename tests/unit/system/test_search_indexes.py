# -*- coding: utf-8 -*-
"""全局搜索 trigram 索引守护（system/search_indexes.py + 迁移快照）。

三类守护：
- **覆盖**：每个搜索提供者的检索字段必须落在「索引清单」或「豁免清单」（新增检索字段
  忘补索引/豁免时红灯——否则会静默退回顺序扫描，且只有上线后才可能被发现）；
- **漂移**：迁移快照（经改名折算）↔ 运行期清单 ↔ 迁移 state_operations 三处
  索引名/表/字段一致（表名折算 = approval/0005 的 ``TRGM_TABLE_RENAMES``，TG-3/ADR-080）；
- **降级与形态**：建索引 SQL 为 pg_trgm GIN 且幂等；非 PostgreSQL 不执行任何 DDL；
  扩展不可用时只告警、不再尝试建索引（不阻断迁移）。
"""

import importlib

import pytest

from system import search_indexes
from system.search import SEARCH_PROVIDERS

# trgm 索引快照按表归属拆在两个迁移里（ADR-058）：system 侧 5 个 + approval 侧 4 个
MIGRATIONS = [
    importlib.import_module(module_path)
    for module_path in (
        "system.migrations.0004_accountrisk_apiapplication_apiapplicationgrant_and_more",
        "approval.migrations.0001_initial",
    )
]

# 表归域改名迁移（TG-3 / ADR-080）：登记表名折算、本身不含 trgm DDL——索引本体
# 随 ALTER TABLE RENAME 自动跟随。漂移守护用它把迁移内历史快照折算到当前表名。
RENAME_MIGRATIONS = [
    importlib.import_module(module_path)
    for module_path in ("approval.migrations.0005_alter_approvaldelegation_table_and_more",)
]


def _folded_snapshot() -> dict:
    """迁移快照（建索引时点的历史表名）→ 折算改名 → 与运行期清单同口径。"""
    snapshot = {}
    for migration in MIGRATIONS:
        snapshot.update({name: (table, field) for name, table, field in migration.TRGM_INDEXES})
    for migration in RENAME_MIGRATIONS:
        renames = migration.TRGM_TABLE_RENAMES
        snapshot = {name: (renames.get(table, table), field) for name, (table, field) in snapshot.items()}
    return snapshot


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

    def test_index_names_fit_postgres_name_limit(self):
        """索引名 ≤ 30 字符（Django 跨库上限；超长触发 models.E034 阻断 manage.py start）。"""
        too_long = [item.name for item in search_indexes.SEARCH_TRGM_INDEXES if len(item.name) > 30]
        assert too_long == [], f"索引名超过 30 字符（models.E034）：{too_long}"

    def test_contrib_postgres_always_registered(self):
        """django.contrib.postgres 必须无条件注册：模型静态含 GinIndex，缺失会让
        `check --database` 报 postgres.E005 卡死服务启动（2026-09-18 部署事故根因）；
        条件化（仅 PG 后端注册）会让 mysql 部署与 CI 默认配置同样踩坑。"""
        from django.conf import settings

        assert "django.contrib.postgres" in settings.INSTALLED_APPS


class TestMigrationDrift:
    def test_snapshot_matches_runtime_registry(self):
        registry = {item.name: (item.table, item.field) for item in search_indexes.SEARCH_TRGM_INDEXES}
        assert _folded_snapshot() == registry, "迁移快照与 system/search_indexes.py 清单漂移（新增字段请补新迁移）"

    def test_rename_registration_covers_only_known_tables(self):
        """改名折算登记只允许引用历史快照里真实存在的表（防登记本身腐化）。"""
        historical_tables = {table for migration in MIGRATIONS for _name, table, _field in migration.TRGM_INDEXES}
        for migration in RENAME_MIGRATIONS:
            unknown = set(migration.TRGM_TABLE_RENAMES) - historical_tables
            assert unknown == set(), f"改名折算登记引用了快照中不存在的表：{unknown}"

    @pytest.mark.parametrize("migration", MIGRATIONS, ids=lambda m: m.__name__)
    def test_state_operations_match_snapshot(self, migration):
        operations = migration.Migration.operations
        state_ops = [op for op in operations if hasattr(op, "state_operations") and op.state_operations]
        assert len(state_ops) == 1, "迁移应仅有单个 SeparateDatabaseAndState（state 声明 + 受控执行）"
        declared = {op.index.name for op in state_ops[0].state_operations}
        assert declared == {name for name, _table, _field in migration.TRGM_INDEXES}


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
    @pytest.mark.parametrize("migration", MIGRATIONS, ids=lambda m: m.__name__)
    def test_migration_skips_non_postgres(self, migration):
        migration._create_indexes(None, _FakeSchemaEditor(_NonPgConnection()))
        migration._drop_indexes(None, _FakeSchemaEditor(_NonPgConnection()))

    @pytest.mark.parametrize("migration", MIGRATIONS, ids=lambda m: m.__name__)
    def test_migration_creates_extension_then_all_indexes(self, migration):
        connection = _RecordingPgConnection()
        migration._create_indexes(None, _FakeSchemaEditor(connection))
        assert connection.log[0] == search_indexes.TRGM_EXTENSION_SQL
        created = connection.log[1:]
        assert len(created) == len(migration.TRGM_INDEXES)
        assert all("USING gin" in sql and sql.startswith("CREATE INDEX IF NOT EXISTS") for sql in created)

    @pytest.mark.parametrize("migration", MIGRATIONS, ids=lambda m: m.__name__)
    def test_migration_degrades_when_extension_unavailable(self, migration):
        connection = _RecordingPgConnection(fail_extension=True)
        migration._create_indexes(None, _FakeSchemaEditor(connection))
        assert connection.log == [], "扩展不可用时不应再尝试建索引（检索回退顺序扫描）"
