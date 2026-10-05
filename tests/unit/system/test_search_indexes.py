# -*- coding: utf-8 -*-
"""全局搜索 trigram 索引守护（system/search_indexes.py + 迁移快照）。

三类守护：
- **覆盖**：每个搜索提供者的检索字段必须落在「索引清单」或「豁免清单」（新增检索字段
  忘补索引/豁免时红灯——否则会静默退回顺序扫描，且只有上线后才可能被发现）；
- **漂移**：迁移快照 ↔ 运行期清单一致（trgm 索引随各域 0001 迁移的模型 Meta GinIndex
  落地，快照即从迁移 operations 提取——只在注册表或只在模型 Meta 出现都会红灯）；
- **扩展与形态**：pg_trgm 扩展由 identity.0001（迁移链最前端）首操作守护——仅 PG 执行、
  不可用只告警不阻断迁移（检索回退顺序扫描）；建索引 SQL 为幂等形态。
"""

import importlib

import pytest
from django.apps import apps
from django.db.migrations import AddIndex, CreateModel

from system import search_indexes
from system.search import SEARCH_PROVIDERS

# trgm 索引随模型 Meta 分属三个域的初始迁移：identity 侧 4 个 + system 侧 1 个 + approval 侧 4 个
MIGRATION_MODULES = (
    "identity.migrations.0001_initial",
    "system.migrations.0001_initial",
    "approval.migrations.0001_initial",
)
MIGRATIONS = [importlib.import_module(module_path) for module_path in MIGRATION_MODULES]

# 扩展守护函数：迁移链最前端（identity.0001）的首个 RunPython，后续所有 GinIndex DDL 依赖它
_ensure_trgm_extension = importlib.import_module("identity.migrations.0001_initial")._ensure_trgm_extension


def _trgm_indexes_of(module) -> dict:
    """迁移 CreateModel 的 GinIndex（gin_trgm_ops）→ {索引名: (表, 字段)}。

    表名经运行期模型解析（各域默认表名，与初始迁移建表名同源）；单字段口径与
    system/search_indexes.py 清单一致，出现复合字段索引说明口径漂移。
    """
    app_label = module.__name__.split(".")[0]
    snapshot = {}
    for op in module.Migration.operations:
        if isinstance(op, AddIndex):
            model_name, index = op.model_name, op.index
        elif isinstance(op, CreateModel):
            model_name = op.name
            index = next(
                (i for i in (op.options or {}).get("indexes") or [] if "gin_trgm_ops" in (i.opclasses or [])),
                None,
            )
        else:
            continue
        if index is None or "gin_trgm_ops" not in (index.opclasses or []):
            continue
        assert len(index.fields) == 1, f"trgm 索引按单字段登记：{index.name}"
        snapshot[index.name] = (apps.get_model(app_label, model_name)._meta.db_table, index.fields[0])
    return snapshot


def _folded_snapshot() -> dict:
    snapshot = {}
    for module in MIGRATIONS:
        snapshot.update(_trgm_indexes_of(module))
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
        assert _folded_snapshot() == registry, (
            "迁移模型 Meta 与 system/search_indexes.py 清单漂移（新增检索字段请同步模型 Meta）"
        )

    @pytest.mark.parametrize("module", MIGRATIONS, ids=lambda m: m.__name__)
    def test_every_domain_migration_carries_trgm_indexes(self, module):
        snapshot = _trgm_indexes_of(module)
        assert snapshot, f"{module.__name__} 应随模型 Meta 携带 trgm 索引"
        bad = [name for name in snapshot if not (name.startswith("idx_") and name.endswith("_trgm"))]
        assert bad == [], f"索引名违反 idx_*_trgm 约定：{bad}"


class TestSqlShape:
    def test_create_index_sql_is_idempotent_trigram(self):
        sql = search_indexes.create_index_sql("identity_userinfo", "username", "idx_userinfo_username_trgm")
        assert sql == (
            "CREATE INDEX IF NOT EXISTS idx_userinfo_username_trgm ON identity_userinfo USING gin (username gin_trgm_ops)"
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
    def test_extension_guard_skips_non_postgres(self):
        _ensure_trgm_extension(None, _FakeSchemaEditor(_NonPgConnection()))

    def test_extension_guard_creates_extension(self):
        connection = _RecordingPgConnection()
        _ensure_trgm_extension(None, _FakeSchemaEditor(connection))
        assert connection.log == [search_indexes.TRGM_EXTENSION_SQL]

    def test_extension_guard_degrades_when_unavailable(self):
        connection = _RecordingPgConnection(fail_extension=True)
        _ensure_trgm_extension(None, _FakeSchemaEditor(connection))
        assert connection.log == [], "扩展不可用时只告警、不再尝试 DDL（检索回退顺序扫描）"
