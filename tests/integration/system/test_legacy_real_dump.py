# -*- coding: utf-8 -*-
"""真实 4.2.5 dump 的原地升级回归（**opt-in**：需要 ``XADMIN_LEGACY_DUMP``）。

CI 默认跳过（无 dump）；在演练机/本地指定 dump 路径即可跑通完整链路，把一次性手工验收
固化为可重放守护：

    导入真实库 → 体检判定「可原地升级」→ 执行对齐 → 数据保全校验

保全校验的口径（对齐只应改变结构、记录与引用，**不该动数据**）：

1. 逐表行数不变——例外：``django_migrations``（账本重写是设计内行为）；
2. 关键数据指纹逐字节一致——例外：存量引用表（``ModelLabelField.name`` /
   ``DataPermission.rules``）只允许出现「模型标签映射」的替换；
3. 二开表（``demo_book*``）外键数量不变、且不再指向任何旧域表（PG 改名自动跟随）；
4. 内容类型行必须都能对应当前模型（旧域标签清零）；
5. 账本：本仓库 app 的记录 == 当前代码链。

用法::

    XADMIN_LEGACY_DUMP=/path/all.sql.tar.gz \\
    XADMIN_LEGACY_DUMP_TAG=v4.2.5 \\
    .venv/bin/python -m pytest tests/integration/system/test_legacy_real_dump.py -q
"""

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import quote

import psycopg
import pytest
from django.apps import apps
from django.db.migrations.loader import MigrationLoader

from system.utils.platform import legacy_align

DUMP_ENV = "XADMIN_LEGACY_DUMP"
TAG_ENV = "XADMIN_LEGACY_DUMP_TAG"
DEFAULT_TAG = "v4.2.5"
SCRATCH_SUFFIX = "_legacy_real"

# 旧表 → 新表（含由父表派生改名的关联表）
RENAMES = {
    "system_userinfo": "identity_userinfo",
    "system_deptinfo": "identity_deptinfo",
    "system_userrole": "identity_userrole",
    "system_uploadfile": "file_uploadfile",
    "system_operationlog": "audit_operationlog",
    "system_userloginlog": "audit_userloginlog",
    "common_monitor": "system_monitor",
    "system_userrole_menu": "identity_userrole_menu",
    "system_userinfo_roles": "identity_userinfo_roles",
    "system_deptinfo_roles": "identity_deptinfo_roles",
    "system_userinfo_rules": "identity_userinfo_rules",
    "system_deptinfo_rules": "identity_deptinfo_rules",
    "system_userinfo_groups": "identity_userinfo_groups",
    "system_userinfo_user_permissions": "identity_userinfo_user_permissions",
}

# 存量「模型标签」改写映射（与 legacy_refs.content_type_moves 同源）
LABEL_MOVES = {
    "system.userinfo": "identity.userinfo",
    "system.deptinfo": "identity.deptinfo",
    "system.userrole": "identity.userrole",
    "system.uploadfile": "file.uploadfile",
    "system.operationlog": "audit.operationlog",
    "system.userloginlog": "audit.userloginlog",
    "common.monitor": "system.monitor",
}

FINGERPRINTS = {
    "system_userinfo": "select id, username, dept_id, is_active from system_userinfo order by id",
    "system_deptinfo": "select id, name, code, parent_id from system_deptinfo order by rank, code",
    "system_userrole": "select id, code, is_active from system_userrole order by code",
    "demo_book": "select id, name, isbn, admin_id, admin2_id, file_id, creator_id from demo_book order by id",
    "demo_book_managers": "select id, book_id, userinfo_id from demo_book_managers order by id",
    "demo_book_managers2": "select id, book_id, userinfo_id from demo_book_managers2 order by id",
    "system_userrole_menu": "select id, userrole_id, menu_id from system_userrole_menu order by id",
    "notifications_messagecontent_notice_dept": (
        "select id, messagecontent_id, deptinfo_id from notifications_messagecontent_notice_dept order by id"
    ),
    "system_modellabelfield": "select id, name from system_modellabelfield order by id",
    "system_datapermission": "select id, name, rules from system_datapermission order by name",
}
# 允许且只允许出现「标签替换」的引用表
REFERENCE_TABLES = {"system_modellabelfield", "system_datapermission"}


@pytest.fixture(autouse=True)
def _allow_legacy_db(django_db_blocker):
    with django_db_blocker.unblock():
        yield


def _db_config() -> dict:
    return dict(__import__("django.conf", fromlist=["settings"]).settings.DATABASES["default"])


def _dsn(db_name: str) -> str:
    config = _db_config()
    return (
        f"postgresql://{quote(config['USER'])}:{quote(config.get('PASSWORD') or '')}"
        f"@{config['HOST']}:{config['PORT']}/{db_name}"
    )


def _psql(dsn: str, sql: str | None = None, sql_file: Path | None = None) -> subprocess.CompletedProcess:
    """用本地 psql 执行 SQL（dump 里的 COPY ... FROM stdin 只有 psql 能直接吃）。"""
    args = ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-q"]
    if sql_file is not None:
        args += ["-f", str(sql_file)]
    return subprocess.run(args, input=sql, capture_output=True, text=True, check=False)


def _require_dump() -> Path:
    if not shutil.which("psql"):
        pytest.skip("本机没有 psql 客户端（导入 dump 需要）")
    raw = os.environ.get(DUMP_ENV, "")
    if not raw:
        pytest.skip(f"未提供真实 4.2.5 dump（{DUMP_ENV} 未设置）")
    path = Path(raw).expanduser()
    if not path.is_file():
        pytest.skip(f"dump 不存在：{path}")
    return path


def _extract_sql(dump: Path, workdir: Path) -> Path:
    if dump.suffix == ".sql":
        return dump
    with tarfile.open(dump) as archive:
        members = [item for item in archive.getmembers() if item.name.endswith(".sql")]
        assert members, f"{dump} 里没有 .sql"
        archive.extract(members[0], workdir, filter="data")
        return workdir / members[0].name


def _split_dump(sql_path: Path, db_name: str, workdir: Path) -> Path:
    """拆掉「集群级」部分并改库名：只保留角色前置段 + 目标库段（目标库由我们自己建）。"""
    lines = sql_path.read_text(encoding="utf-8", errors="replace").splitlines()
    connect_index = next(
        (index for index, line in enumerate(lines) if line.startswith("\\connect ") and "xadmin" in line),
        None,
    )
    assert connect_index is not None, "dump 里找不到 xadmin 库的 \\connect 段"
    # 集群级段（角色 / template1 / postgres / CREATE DATABASE）全部丢掉：目标库由用例自己建，
    # 留着会试图创建 ``xadmin`` 集群库（与目标库名不同，导入必然失败）
    drop_prefixes = ("CREATE ROLE", "ALTER ROLE", "CREATE DATABASE", "ALTER DATABASE")
    first_connect = next((index for index, line in enumerate(lines) if line.startswith("\\connect ")), None)
    assert first_connect is not None
    head = [line for line in lines[:first_connect] if not line.startswith(drop_prefixes)]
    body = [
        line.replace("\\connect xadmin", f"\\connect {db_name}")
        for line in lines[connect_index:]
        if not line.startswith(drop_prefixes)
    ]
    target = workdir / "import.sql"
    target.write_text("\n".join(head + body) + "\n", encoding="utf-8")
    return target


def _import_dump(dump: Path, db_name: str) -> str:
    with tempfile.TemporaryDirectory(prefix="xadmin-legacy-dump-") as tmp:
        workdir = Path(tmp)
        sql_path = _extract_sql(dump, workdir)
        script = _split_dump(sql_path, db_name, workdir)
        admin = _db_config()
        with (
            psycopg.connect(
                host=admin["HOST"],
                port=admin["PORT"],
                dbname="postgres",
                user=admin["USER"],
                password=admin.get("PASSWORD") or "",
                autocommit=True,
            ) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(f'drop database if exists "{db_name}" with (force)')
            cursor.execute(f'create database "{db_name}" owner "{admin["USER"]}"')
        result = _psql(_dsn(db_name), sql_file=script)
        assert result.returncode == 0, f"导入失败：{result.stderr[-2000:]}"
    return _dsn(db_name)


def _drop_database(db_name: str) -> None:
    admin = _db_config()
    with (
        psycopg.connect(
            host=admin["HOST"],
            port=admin["PORT"],
            dbname="postgres",
            user=admin["USER"],
            password=admin.get("PASSWORD") or "",
            autocommit=True,
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(f'drop database if exists "{db_name}" with (force)')


def _resolve(sql: str) -> str:
    for source in sorted(RENAMES, key=len, reverse=True):
        sql = sql.replace(source, RENAMES[source])
    return sql


def _snapshot(dsn: str, applied: bool) -> dict:
    resolve = _resolve if applied else (lambda sql: sql)
    snapshot: dict = {"counts": {}, "prints": {}}
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute("select table_name from information_schema.tables where table_schema='public'")
        for (table,) in cursor.fetchall():
            cursor.execute(f'select count(*) from "{table}"')
            snapshot["counts"][table] = cursor.fetchone()[0]
        for name, sql in FINGERPRINTS.items():
            cursor.execute(resolve(sql))
            snapshot["prints"][name] = [[str(value) for value in row] for row in cursor.fetchall()]
        cursor.execute("select app_label, model from django_content_type")
        snapshot["content_types"] = cursor.fetchall()
        cursor.execute("select app, name from django_migrations")
        snapshot["ledger"] = cursor.fetchall()
        cursor.execute(
            "select count(*), coalesce(array_agg(distinct ccu.table_name)::text, '{}') from pg_constraint c"
            " join information_schema.constraint_column_usage ccu on ccu.constraint_name = c.conname"
            " where c.conrelid::regclass::text like 'demo_book%' and c.contype = 'f'"
        )
        snapshot["demo_fk_count"], snapshot["demo_fk_targets"] = cursor.fetchone()
    return snapshot


def _apply_label_moves(rows: list) -> list:
    moved = []
    for row in rows:
        values = list(row)
        for index, value in enumerate(values):
            for source, target in LABEL_MOVES.items():
                value = value.replace(source, target)
            values[index] = value
        moved.append(values)
    return moved


def _project_ledger() -> set:
    loader = MigrationLoader(None, ignore_no_migrations=False)
    return {
        (app, name)
        for (app, name), migration in loader.disk_migrations.items()
        if legacy_align.is_project_migration(migration)
    }


def test_real_dump_upgrades_in_place_without_data_loss():
    dump = _require_dump()
    tag = os.environ.get(TAG_ENV, DEFAULT_TAG)
    db_name = f"{_db_config()['NAME']}{SCRATCH_SUFFIX}"
    dsn = _import_dump(dump, db_name)
    try:
        # 预演：真实库必须判「可原地升级」且无阻断项
        report = legacy_align.build_steps(dsn, tag=tag)["report"]
        assert report["blockers"] == [], report["blockers"]
        assert report["renamed"], "真实 4.2.5 库应当有待改名表"

        before = _snapshot(dsn, applied=False)
        result = legacy_align.align_full(dsn, tag=tag, dry_run=False)
        assert result["after"]["blockers"] == []
        assert result["after"]["renamed_left"] == 0
        assert result["after"]["missing_tables"] == []
        assert result["aligned"] is True
        after = _snapshot(dsn, applied=True)

        # 1) 行数：除账本外逐表不变
        for table, count in before["counts"].items():
            if table == "django_migrations":
                continue
            assert after["counts"].get(RENAMES.get(table, table)) == count, f"{table} 行数变化"

        # 2) 指纹：引用表只允许标签替换；其余逐字节一致
        for name, rows in before["prints"].items():
            expected = _apply_label_moves(rows) if name in REFERENCE_TABLES else rows
            assert after["prints"][name] == expected, f"{name} 数据被改动"

        # 3) 二开表外键：数量不变、目标全部跟随改名
        assert after["demo_fk_count"] == before["demo_fk_count"]
        assert not [item for item in after["demo_fk_targets"] if item.startswith(("system_", "common_"))]

        # 4) 内容类型：全部能对应当前模型（旧域标签清零）
        known = {(model._meta.app_label, model._meta.model_name) for model in apps.get_models()}
        assert [(app, model) for app, model in after["content_types"] if (app, model) not in known] == []

        # 5) 账本：本仓库 app 记录 == 当前链
        expected_ledger = _project_ledger()
        project_apps = {app for app, _ in expected_ledger}
        assert {(app, name) for app, name in after["ledger"] if app in project_apps} == expected_ledger
        assert json.dumps(before["counts"]) != json.dumps(after["counts"])  # 账本确实被重写
    finally:
        _drop_database(db_name)
