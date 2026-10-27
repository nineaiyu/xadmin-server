#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""结构补齐（原地升级）：缺失表 / 外键列 / 索引与约束 / 前置扩展，全部用 Django 的语义建出来。

为什么不用手写 DDL：目标态就是「``migrate`` 在空库上建出来的结构」。这里逐条执行迁移链里的
``CreateModel`` / ``AddField`` 与模型 ``Meta`` 上的索引/约束，因此 DDL、命名、索引表达式都与
全新安装完全一致；已对齐的表跳过不重建（它们由改名 + 补列负责），外键目标因此总是已存在。

前置操作（``RunPython``）按白名单执行（pgvector / pg_trgm 扩展，幂等且必须先于建表）；
本仓库 app 的名单外 ``RunPython`` 一律不执行并报告，三方 app 的 ``RunPython`` 交由常规 ``migrate``。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from ops.legacy import schema as legacy_schema

LEGACY_CONNECTION_ALIAS = "_legacy_inplace"


def is_project_migration(migration: Any) -> bool:
    """迁移是否属于**本仓库**的 app（区别于 auth / celery / token_blacklist 等三方）。

    这条区分决定「谁负责」：本仓库 app 的迁移链是清库重建式产物，结构由对齐器负责、账本需要重写；
    三方 app 的迁移是真正的增量链（旧库已应用、版本升级时按常规 ``migrate`` 执行增量），
    因此既不重写其账本、也不执行其 ``RunPython``。
    """
    import sys
    from pathlib import Path

    module = sys.modules.get(migration.__module__)
    path = getattr(module, "__file__", "") or ""
    if not path:
        return False
    resolved = Path(path).resolve()
    root = Path(__file__).resolve().parents[2]
    if not resolved.is_relative_to(root):
        return False
    # 依赖目录就在仓库内（.venv/），必须排除：否则三方 app 会被当成项目自带
    return not set(resolved.parts) & {"site-packages", "dist-packages", ".venv", "node_modules"}


def _register_connection(dsn: str) -> Any:
    """把旧库以临时别名注册进 Django 连接表并返回连接。

    两个约束决定了这个形态：

    - **必须走 ``connections`` 注册表**：Django 的 ``connection_created`` 信号
      （``contrib.postgres`` 的类型适配器注册）与部分内部逻辑按 ``connection.alias`` 反查连接，
      手搓 ``DatabaseWrapper`` 会在建连时抛 ``AttributeError``；
    - **注册时机必须在运行期**：测试进程里若别名在 ``settings.DATABASES`` 里被测试装置看到，
      它会为该别名建立/重定向测试库，出现「配置指向旧库、实际连到 test_* 库」的静默错位。
    """
    from django.db import connections
    from psycopg.conninfo import conninfo_to_dict

    info = conninfo_to_dict(dsn)
    config = dict(connections.databases["default"])  # 以默认配置为底（连接构造对 settings 键有硬要求）
    config.update(
        {
            "NAME": info.get("dbname") or info.get("database"),
            "USER": info.get("user", ""),
            "PASSWORD": info.get("password", ""),
            "HOST": info.get("host", ""),
            "PORT": info.get("port", ""),
            "ATOMIC_REQUESTS": False,
            "TEST": {},
            # 不用连接池：默认配置里可能带 psycopg_pool，共享池会与被测库互相干扰（连接被回收后复用）
            "OPTIONS": {},
            "CONN_MAX_AGE": 0,
        }
    )
    connections.databases[LEGACY_CONNECTION_ALIAS] = config
    # 一个进程连续处理多个库（演练 / 版本家族回归）时必须丢弃缓存的包装器：
    # 否则会拿到上一个库的连接，静默把 DDL 打到错的库上
    try:
        del connections[LEGACY_CONNECTION_ALIAS]
    except AttributeError:  # pragma: no cover - 未缓存过
        pass
    return connections[LEGACY_CONNECTION_ALIAS]


def dsn_from_connection(connection: Any) -> str:
    """由 Django 连接配置拼出 DSN（结构引擎走 psycopg，需要连接串）。"""
    from urllib.parse import quote

    settings_dict = connection.settings_dict
    return (
        f"postgresql://{quote(settings_dict['USER'])}:{quote(settings_dict.get('PASSWORD') or '')}"
        f"@{settings_dict['HOST']}:{settings_dict['PORT']}/{settings_dict['NAME']}"
    )


def connect_legacy(dsn: str) -> Any:
    """对外入口：连接一个外部（旧）库（体检 / 对齐共用同一套连接约定）。"""
    return _register_connection(dsn)


def close_legacy(connection: Any) -> None:
    """对外入口：关闭外部库连接。"""
    _release_connection(connection)


def _release_connection(connection: Any) -> None:
    """关闭临时连接（保留别名注册：连接回收后仍可能被信号处理器按别名反查）。"""
    connection.close()


@contextmanager
def _atomic(connection: Any) -> Any:
    """单事务执行（失败整体回滚）：不依赖 ``transaction.atomic`` 的全局别名。"""
    connection.set_autocommit(False)
    try:
        yield
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.set_autocommit(True)


PREREQUISITE_CALLABLES = (
    "create_vector_extension",
    "_ensure_trgm_extension",
    # 幂等数据清理（删除已废弃的登录日志 retrieve 权限点），存量库结构对齐时执行
    "remove_dead_permission",
    # 验证码风险规则默认行（bulk_create + ignore_conflicts，幂等）
    "seed_rules",
)


def _db_index_names(connection: Any) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("select indexname from pg_indexes where schemaname = current_schema()")
        return {row[0] for row in cursor.fetchall()}


def _db_constraint_names(connection: Any) -> set[str]:
    """约束名集合。

    Django 的 ``UniqueConstraint`` 在 PostgreSQL 上可能落成**唯一索引**（``CREATE UNIQUE INDEX``）
    而不是表约束，因此判存在必须把索引名并进来——否则会重复创建并撞名。
    """
    with connection.cursor() as cursor:
        cursor.execute("select conname from pg_constraint where connamespace = current_schema()::regnamespace")
        names = {row[0] for row in cursor.fetchall()}
    return names | _db_index_names(connection)


def _ordered_nodes(loader: Any) -> list[tuple[str, str]]:
    """全部迁移节点的拓扑序（各叶子节点的 forwards_plan 合并去重）。"""
    ordered: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for leaf in sorted(loader.graph.leaf_nodes()):
        for node in loader.graph.forwards_plan(leaf):
            if node not in seen:
                seen.add(node)
                ordered.append(node)
    return ordered


def _run_prerequisites(loader: Any, editor: Any, nodes: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """执行白名单内的前置 RunPython（扩展创建等）；名单外的一律跳过并报告。"""
    from django.db.migrations.operations.special import RunPython, RunSQL

    ran: list[str] = []
    skipped: list[str] = []
    for node in nodes:
        migration = loader.disk_migrations.get(node)
        if migration is None:
            continue
        state = loader.project_state([node])
        for operation in migration.operations:
            if isinstance(operation, RunSQL):
                skipped.append(f"{node[0]}.{node[1]}: RunSQL 未登记为前置操作，跳过")
            elif isinstance(operation, RunPython):
                name = getattr(operation.code, "__name__", "")
                if not is_project_migration(migration):
                    # 三方 app 的数据迁移由它们自己的增量链负责（升级时按常规 migrate 执行）
                    skipped.append(f"{node[0]}.{node[1]}: RunPython {name} 属三方 app，交由 migrate 处理")
                    continue
                if name not in PREREQUISITE_CALLABLES:
                    skipped.append(f"{node[0]}.{node[1]}: RunPython {name or '<lambda>'} 未登记为前置操作，跳过")
                    continue
                # 前置操作只做 SQL（扩展创建）：from/to 传同一状态，避免 initial 迁移
                # 空依赖导致状态里没有自身 app；白名单守护保证不会跑数据回填类操作
                operation.database_forwards(node[0], editor, state, state)
                ran.append(name)
    return ran, skipped


def _create_missing_tables(connection: Any, loader: Any, editor: Any, nodes: list[tuple[str, str]]) -> list[str]:
    """按迁移顺序执行 ``CreateModel`` 补齐缺失表（DDL 完全出自 Django，含模型级索引/约束）。"""
    from django.db.migrations.operations.models import CreateModel

    created: list[str] = []
    existing = set(connection.introspection.table_names())
    for node in nodes:
        migration = loader.disk_migrations.get(node)
        if migration is None:
            continue
        state = loader.project_state([node])
        for operation in migration.operations:
            if not isinstance(operation, CreateModel):
                continue
            model = state.apps.get_model(node[0], operation.name)
            table = model._meta.db_table
            if table in existing or not operation.allow_migrate_model(connection.alias, model):
                continue
            operation.database_forwards(node[0], editor, None, state)
            created.append(table)
            existing.add(table)
    return sorted(created)


def _create_missing_through_tables(connection: Any, editor: Any) -> list[str]:
    """补建**已对齐模型**上新增多对多的中间表。

    这类表既不在 ``CreateModel``（模型本身已存在、被跳过）里，也不是 ``AddField``（多对多在
    初始建表时就声明了），只能按「模型的 through 表是否在库里」单独补齐。
    """
    from django.apps import apps

    tables = set(connection.introspection.table_names())
    created: list[str] = []
    for model in apps.get_models(include_auto_created=True, include_swapped=True):
        if model._meta.auto_created:
            continue
        for field in model._meta.local_many_to_many:
            through = getattr(field.remote_field, "through", None)
            if through is None or not through._meta.auto_created:
                continue
            table = through._meta.db_table
            if table in tables:
                continue
            editor.create_model(through)
            tables.add(table)
            created.append(table)
    return sorted(created)


def _add_missing_model_fields(connection: Any, editor: Any) -> list[str]:
    """按**当前模型**补齐缺失列（用 Django 的 ``add_field``）。

    对已对齐的表，迁移链里的 ``CreateModel`` 是「初始态」——后来新增的列（例如默认值可调用、
    不可写成 SQL 字面量的列表字段）没有对应的 ``AddField`` 操作，只能按当前模型补；
    对新建的表，0002 里的外键列也在此一并补齐。``add_field`` 与 ``migrate`` 完全同语义：
    NOT NULL 列用字段默认值回填既有行、随后去掉服务端默认。
    """
    from django.apps import apps

    added: list[str] = []
    tables = set(connection.introspection.table_names())
    columns_cache: dict[str, set[str]] = {}
    for model in apps.get_models(include_auto_created=True, include_swapped=True):
        table = model._meta.db_table
        if table not in tables:
            continue
        for field in model._meta.local_fields:
            if field.column is None or field.many_to_many:
                continue
            if table not in columns_cache:
                with connection.cursor() as cursor:
                    description = connection.introspection.get_table_description(cursor, table)
                columns_cache[table] = {item.name for item in description}
            if field.column in columns_cache[table]:
                continue
            editor.add_field(model, field)
            columns_cache[table].add(field.column)
            added.append(f"{table}.{field.column}")
    return sorted(added)


def _create_missing_indexes(connection: Any, editor: Any) -> list[str]:
    """补建模型 ``Meta.indexes`` 里声明、但库里还没有的索引（按名字判存在）。"""
    from django.apps import apps

    existing_tables = set(connection.introspection.table_names())
    names = _db_index_names(connection)
    created: list[str] = []
    for model in apps.get_models(include_auto_created=True, include_swapped=True):
        table = model._meta.db_table
        if table not in existing_tables:
            continue
        for index in model._meta.indexes:
            if index.name in names:
                continue
            editor.add_index(model, index)
            names.add(index.name)
            created.append(f"{table}.{index.name}")
    return sorted(created)


def _create_missing_constraints(connection: Any, editor: Any) -> list[str]:
    """补建模型 ``Meta.constraints`` 里声明、但库里还没有的约束（按名字判存在）。"""
    from django.apps import apps

    existing_tables = set(connection.introspection.table_names())
    names = _db_constraint_names(connection)
    created: list[str] = []
    for model in apps.get_models(include_auto_created=True, include_swapped=True):
        table = model._meta.db_table
        if table not in existing_tables:
            continue
        for constraint in model._meta.constraints:
            if not constraint.name or constraint.name in names:
                continue
            editor.add_constraint(model, constraint)
            names.add(constraint.name)
            created.append(f"{table}.{constraint.name}")
    return sorted(created)


def complete_structure(dsn: str) -> dict[str, Any]:
    """补齐结构：前置操作（扩展）→ 缺失表 → 缺失索引 → 缺失约束（单事务）。

    已对齐的表不重建（它们由改名 + 补列负责），只把「库里还没有的对象」按 Django 的方式建出来，
    因此最终结构与 ``migrate`` 在空库上建出的结构一致，且外键目标总是已存在。
    """
    from django.db.migrations.loader import MigrationLoader

    connection = _register_connection(dsn)
    result: dict[str, Any] = {"prerequisites": [], "skipped": [], "tables": [], "indexes": [], "constraints": []}
    try:
        loader = MigrationLoader(connection, ignore_no_migrations=False)
        nodes = _ordered_nodes(loader)
        # 两段式：建表阶段的索引 SQL 由 schema editor 攒在 deferred_sql、退出时才执行，
        # 若与「补索引」同段，补索引会先建出来、随后 deferred SQL 撞名（DuplicateTable）。
        with _atomic(connection):
            with connection.schema_editor(atomic=False) as editor:
                result["prerequisites"], result["skipped"] = _run_prerequisites(loader, editor, nodes)
                result["tables"] = _create_missing_tables(connection, loader, editor, nodes)
                result["through_tables"] = _create_missing_through_tables(connection, editor)
                result["fields"] = _add_missing_model_fields(connection, editor)
            with connection.schema_editor(atomic=False) as editor:
                result["indexes"] = _create_missing_indexes(connection, editor)
                result["constraints"] = _create_missing_constraints(connection, editor)
    finally:
        _release_connection(connection)
    result["count"] = len(result["tables"])
    return result


def missing_columns(dsn: str) -> list[str]:
    """已存在的表里、当前模型有而库里还没有的列（只读，供预演）。"""
    import psycopg
    from django.apps import apps

    catalog = legacy_schema.introspect_schema(dsn)
    missing: list[str] = []
    for model in apps.get_models(include_auto_created=True, include_swapped=True):
        table = model._meta.db_table
        columns = catalog.get(table)
        if columns is None:
            continue
        for field in model._meta.local_fields:
            if field.column is None or field.many_to_many:
                continue
            if field.column not in columns:
                missing.append(f"{table}.{field.column}")
    del psycopg
    return sorted(missing)


def missing_tables(dsn: str) -> list[str]:
    """目标结构里存在、当前库中缺失的表（只读）。"""
    target = legacy_schema.target_schema()
    existing = set(legacy_schema.introspect_schema(dsn))
    return sorted(set(target) - existing)


def missing_indexes(dsn: str) -> list[str]:
    """模型 ``Meta.indexes`` 声明、库里还没有的索引（只读，供预演）。"""
    import psycopg
    from django.apps import apps

    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute("select indexname from pg_indexes where schemaname = current_schema()")
        names = {row[0] for row in cursor.fetchall()}
        cursor.execute("select table_name from information_schema.tables where table_schema = 'public'")
        tables = {row[0] for row in cursor.fetchall()}
    return sorted(
        f"{model._meta.db_table}.{index.name}"
        for model in apps.get_models(include_auto_created=True, include_swapped=True)
        if model._meta.db_table in tables
        for index in model._meta.indexes
        if index.name not in names
    )


def missing_constraints(dsn: str) -> list[str]:
    """模型 ``Meta.constraints`` 声明、库里还没有的约束（只读，供预演）。"""
    import psycopg
    from django.apps import apps

    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute("select conname from pg_constraint where connamespace = current_schema()::regnamespace")
        names = {row[0] for row in cursor.fetchall()}
        cursor.execute("select indexname from pg_indexes where schemaname = current_schema()")
        names |= {row[0] for row in cursor.fetchall()}
        cursor.execute("select table_name from information_schema.tables where table_schema = 'public'")
        tables = {row[0] for row in cursor.fetchall()}
    return sorted(
        f"{model._meta.db_table}.{constraint.name}"
        for model in apps.get_models(include_auto_created=True, include_swapped=True)
        if model._meta.db_table in tables
        for constraint in model._meta.constraints
        if constraint.name and constraint.name not in names
    )


# 链上的「前置操作」白名单（迁移里以 RunPython 承担，幂等且必须先于建表）
#   create_vector_extension  —— pgvector 扩展（ai 域向量列依赖）
#   _ensure_trgm_extension   —— pg_trgm 扩展（检索索引依赖）
# 新增 RunPython/RunSQL 时若不在名单内一律**不执行**并报告：未知操作可能是数据回填，
# 在结构对齐路径上盲目执行会写坏数据（守护测试锁住这条口径）。
