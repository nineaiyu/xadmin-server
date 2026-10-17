#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""旧结构样本库生成器（真·原地升级的验证靶，独立脚本）。

用途：用**历史 tag 的迁移文件**在任意 PostgreSQL 上生成一个「四域切分之前」的真实结构库，
可选灌入一批关系完整的业务数据。为什么可行：旧迁移是标准 Django 迁移，**当前 Django
能直接执行它们**（只需要一个只装旧 app 迁移的临时工程 + `AUTH_USER_MODEL` 的最小存根），
于是旧表、列、索引、外键与**旧迁移账本**都被真实地建出来——不需要旧版本依赖、不需要
客户库导出，可作为对齐器的可复现验证靶与回归样本。

产出与用法::

    # 目标库不存在会自动建（--fresh 会先删后建）
    python ops/legacy_sample_db.py --tag v4.2.5 \\
        --dsn "postgresql://server:pgtest@127.0.0.1:55433/xadmin_legacy_sample" --with-data

    python manage.py legacy_schema_report --dsn "<同上>" --tag v4.2.5 --plan

注意：本脚本只服务测试与演练（生成样本、灌样本数据），不参与生产升级路径；
样本数据是固定的最小业务集（部门三层 / 角色两个 / 用户两个 / 数据权限 / 系统与个人配置 /
附件 / 公告与已读），用于验证主键、关联与状态在结构对齐后是否原样保留。
"""

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
KERNEL_PATH = REPO_ROOT / "packages" / "xadmin-common"
LEGACY_APPS = ("system", "notifications", "settings", "captcha", "common")
_SEED_FILE = Path(__file__).resolve().parent / "legacy_sample_data.json"

# 固定主键：便于断言「结构对齐后主键 / UUID 未变」。类型按 4.2.5 结构（UUID 域与自增域混用）
DEPT_ROOT, DEPT_MID, DEPT_LEAF = (uuid.UUID(f"11111111-1111-1111-1111-1111111111{n:02d}") for n in (0, 1, 2))
ROLE_DEV, ROLE_QA = (uuid.UUID(f"22222222-2222-2222-2222-2222222222{n:02d}") for n in (0, 1))
RULE_SELF = uuid.UUID("33333333-3333-3333-3333-333333333333")
MENU_PK = uuid.UUID("44444444-4444-4444-4444-444444444401")
META_PK = uuid.UUID("44444444-4444-4444-4444-444444444402")
CONFIG_PK = uuid.UUID("55555555-5555-5555-5555-555555555501")
USER_A, USER_B = 900001, 900002
FILE_PK, NOTICE_PK, PERSONAL_PK = 900001, 900001, 900001
OLD_PASSWORD = "Legacy@2026!"
NOTICE_TITLE = "旧库公告-结构对齐样本"
FILE_NAME = "legacy-sample.txt"
FILE_STORAGE = "upload/legacy-sample.txt"

_SETTINGS_TEMPLATE = '''"""旧结构样本工程（脚本临时生成）：只装旧 app 的迁移。"""

import sys

sys.path.insert(0, {kernel!r})
sys.path.insert(0, {workdir!r})

SECRET_KEY = "legacy-sample-only"
DEBUG = False
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "system.UserInfo"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    # 三方组件的迁移也进样本：真实旧库里 celery 表同样存在，缺了会让样本比真库更「空」
    "django_celery_beat",
    "django_celery_results",
    {apps},
]

DATABASES = {{
    "default": {{
        "ENGINE": "django.db.backends.postgresql",
        "NAME": {name!r},
        "USER": {user!r},
        "PASSWORD": {password!r},
        "HOST": {host!r},
        "PORT": {port!r},
    }}
}}
'''

_STUB_HEADER = '''"""旧结构样本工程的最小模型存根。

真实旧库的 content type / 权限行是按当时的模型建出来的；样本工程只有迁移，若不给存根，
``post_migrate`` 就没有模型可登记，样本会比真库「缺一半」——结构对齐后的内容类型重指与权限
一致性也就无从验证。这里按历史迁移里的模型名生成最小存根：**不参与建表**（结构只由迁移产生），
只让 app registry 与 content type / 权限的行数与真库同构。
"""

from django.contrib.auth.models import AbstractUser
from django.db import models
'''

_USER_STUB = '''

class UserInfo(AbstractUser):
    """`AUTH_USER_MODEL` 目标：``django.contrib.auth`` 启动即需要它。"""

    class Meta:
        app_label = "system"
        db_table = "system_userinfo"
'''


def _model_stub(name: str, app: str, table: str) -> str:
    return f'''

class {name}(models.Model):
    class Meta:
        app_label = "{app}"
        db_table = "{table}"
'''


_MIGRATE_RUNNER = """
import django
from django.core.management import call_command

django.setup()
call_command("migrate", verbosity=0, interactive=False)
print("migrate ok")
"""


# ---------------------------------------------------------------------------
# 工程与结构
# ---------------------------------------------------------------------------


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{result.stderr.strip()}")
    return result.stdout


def export_migrations(tag: str, workdir: Path) -> int:
    """把 tag 里旧 app 的迁移文件导出到临时工程（保持 `<app>/migrations/` 布局）。"""
    listing = _git("ls-tree", "-r", "--name-only", tag).split()
    prefixes = tuple(f"{app}/migrations/" for app in LEGACY_APPS)
    count = 0
    for path in listing:
        if not path.endswith(".py") or not path.startswith(prefixes):
            continue
        target = workdir / "legacy" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_git("show", f"{tag}:{path}"), encoding="utf-8")
        count += 1
    if not count:
        raise SystemExit(f"tag {tag} 下没有匹配 {LEGACY_APPS} 的迁移文件")
    for app in LEGACY_APPS:
        package = workdir / "legacy" / app
        if not package.is_dir():
            continue
        (package / "__init__.py").touch()
        migrations = package / "migrations"
        if migrations.is_dir():
            init = migrations / "__init__.py"
            if not init.exists():
                init.touch()
    (workdir / "legacy").joinpath("__init__.py").touch()
    write_stub_models(workdir, tag)
    return count


def write_stub_models(workdir: Path, tag: str) -> int:
    """按历史迁移里的模型名生成最小存根（只为让 content type / 权限与真库同构）。"""
    if str(REPO_ROOT) not in sys.path:  # 独立脚本自举：保证能导入仓库内的解析器
        sys.path.insert(0, str(REPO_ROOT))
    from system.utils.platform import legacy_schema

    models = legacy_schema.load_tag_models(tag)
    grouped: dict[str, list[tuple[str, str]]] = {}
    for table, meta in models.items():
        grouped.setdefault(meta["app"], []).append((meta["model"], table))
    for app, entries in grouped.items():
        source = [_STUB_HEADER]
        if app == "system":
            source.append(_USER_STUB)
        for name, table in sorted(entries):
            if app == "system" and name == "UserInfo":
                continue
            source.append(_model_stub(name, app, table))
        package = workdir / "legacy" / app
        package.mkdir(parents=True, exist_ok=True)
        (package / "models.py").write_text("\n".join(source), encoding="utf-8")
    return len(models)


def write_settings(workdir: Path, params: dict) -> None:
    apps = ", ".join(repr(f"legacy.{app}") for app in LEGACY_APPS)
    (workdir / "settings.py").write_text(
        _SETTINGS_TEMPLATE.format(
            kernel=str(KERNEL_PATH),
            workdir=str(workdir),
            apps=apps,
            name=params["dbname"],
            user=params["user"],
            password=params["password"],
            host=params["host"],
            port=params["port"],
        ),
        encoding="utf-8",
    )


def run_migrate(workdir: Path, params: dict) -> None:
    """用**当前解释器与当前 Django**执行旧迁移（这是本脚本成立的关键）。"""
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "settings",
        "PYTHONPATH": os.pathsep.join([str(workdir), str(KERNEL_PATH)]),
    }
    result = subprocess.run(
        [sys.executable, "-c", _MIGRATE_RUNNER],
        cwd=str(workdir),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"旧迁移执行失败：\n{result.stdout}\n{result.stderr}")


# ---------------------------------------------------------------------------
# 数据库
# ---------------------------------------------------------------------------


def parse_dsn(dsn: str) -> dict:
    from psycopg.conninfo import conninfo_to_dict

    info = conninfo_to_dict(dsn)
    return {
        "dbname": info.get("dbname") or info.get("database") or "",
        "user": info.get("user") or os.environ.get("USER", ""),
        "password": info.get("password") or "",
        "host": info.get("host") or "127.0.0.1",
        "port": info.get("port") or "5432",
    }


def ensure_database(params: dict, fresh: bool) -> None:
    import psycopg

    admin = psycopg.connect(
        host=params["host"],
        port=params["port"],
        dbname="postgres",
        user=params["user"],
        password=params["password"],
        autocommit=True,
        connect_timeout=5,
    )
    with admin, admin.cursor() as cursor:
        if fresh:
            cursor.execute(f'drop database if exists "{params["dbname"]}" with (force)')
        cursor.execute("select 1 from pg_database where datname = %s", (params["dbname"],))
        if cursor.fetchone() is None:
            cursor.execute(f'create database "{params["dbname"]}"')


def _drop_database(params: dict) -> None:
    import psycopg

    with (
        psycopg.connect(
            host=params["host"],
            port=params["port"],
            dbname="postgres",
            user=params["user"],
            password=params["password"],
            autocommit=True,
            connect_timeout=5,
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(f'drop database if exists "{params["dbname"]}" with (force)')


def _insert(cursor, table: str, row: dict) -> None:
    """按键存在性插入（跨版本列集不同也能跑）：只写该表真正拥有的列。"""
    cursor.execute(
        "select column_name from information_schema.columns where table_schema = 'public' and table_name = %s",
        (table,),
    )
    existing = {item[0] for item in cursor.fetchall()}
    payload = {key: value for key, value in row.items() if key in existing}
    if not payload:
        return
    columns = ", ".join(f'"{name}"' for name in payload)
    placeholders = ", ".join(["%s"] * len(payload))
    cursor.execute(f'insert into "{table}" ({columns}) values ({placeholders})', list(payload.values()))


def _password_hash(password: str) -> str:
    """Django 口径的 PBKDF2 哈希（不依赖 Django 设置，纯标准库生成）。"""
    iterations = 600000
    salt = base64.b64encode(os.urandom(12)).decode().strip("=")[:12]
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations, dklen=32)
    return f"pbkdf2_sha256${iterations}${salt}${base64.b64encode(digest).decode()}"


def seed_data(dsn: str) -> dict:
    """灌入最小业务数据（关系完整：多层部门 / 角色菜单 / 数据权限 / 公告定向与已读）。"""
    import psycopg

    content = f"sample attachment for {FILE_NAME}\n".encode()
    md5 = hashlib.md5(content, usedforsecurity=False).hexdigest()  # noqa: S324 - 旧结构字段仅存摘要
    rows_by_table = load_seed_rows(md5, len(content))
    counts = {}
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        for table, rows in rows_by_table.items():
            for row in rows:
                _insert(cursor, table, row)
            counts[table] = len(rows)
        connection.commit()
    return counts


def load_seed_rows(md5: str, filesize: int) -> dict:
    """读取样本数据文件并填充占位符（密码哈希 / 附件摘要与大小）。

    数据放在同目录的 ``legacy_sample_data.json``：一是让「结构」与「样本数据」分开维护，
    二是避免脚本里堆一大段常量（文件与函数长度门禁）。
    """
    payload = json.loads(_SEED_FILE.read_text(encoding="utf-8"))["rows"]
    replacements = {
        "__PASSWORD_HASH__": _password_hash(OLD_PASSWORD),
        "__MD5__": md5,
        "__FILESIZE__": str(filesize),
    }

    def fill(value):
        if isinstance(value, str) and value in replacements:
            return replacements[value]
        if isinstance(value, dict):
            return {key: fill(item) for key, item in value.items()}
        if isinstance(value, list):
            return [fill(item) for item in value]
        return value

    return {table: [fill(row) for row in rows] for table, rows in payload.items()}


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def summarize(dsn: str) -> dict:
    import psycopg

    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute("select count(*) from information_schema.tables where table_schema = 'public'")
        tables = cursor.fetchone()[0]
        cursor.execute("select app, count(*) from django_migrations group by app order by app")
        ledger = {app: count for app, count in cursor.fetchall()}
    return {"tables": tables, "ledger": ledger}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="用历史 tag 生成旧结构样本库（真·原地升级验证靶）")
    parser.add_argument("--tag", default="v4.2.5", help="历史 tag（默认 v4.2.5）")
    parser.add_argument("--dsn", required=True, help="目标库连接串（库不存在会自动建）")
    parser.add_argument("--with-data", action="store_true", help="灌入最小业务数据")
    parser.add_argument("--keep", action="store_true", help="保留临时工程目录（默认执行后删除）")
    parser.add_argument("--drop", action="store_true", help="只删除目标库（清理用，不做生成）")
    parser.add_argument("--workdir", default="", help="指定临时工程目录（默认系统临时目录）")
    parser.add_argument("--meta-out", default="", help="把样本元信息（tag / 计数 / 固定主键）写入 JSON 文件")
    args = parser.parse_args(argv)

    params = parse_dsn(args.dsn)
    if not params["dbname"]:
        raise SystemExit("dsn 里缺少库名")
    if args.drop:
        _drop_database(params)
        print(f"已删除样本库：{params['dbname']}")
        return 0
    ensure_database(params, fresh=True)

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="xadmin-legacy-sample-"))
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        exported = export_migrations(args.tag, workdir)
        write_settings(workdir, params)
        run_migrate(workdir, params)
        print(f"样本库已建：{params['dbname']}（tag {args.tag}，迁移文件 {exported} 个）")
        counts = seed_data(args.dsn) if args.with_data else {}
        summary = summarize(args.dsn)
        print(f"  · 表 {summary['tables']} 张；旧账本 {summary['ledger']}")
        if counts:
            print(f"  · 样本数据：{', '.join(f'{table}={count}' for table, count in sorted(counts.items()))}")
        if args.meta_out:
            Path(args.meta_out).write_text(
                json.dumps(
                    {
                        "tag": args.tag,
                        "database": params["dbname"],
                        "tables": summary["tables"],
                        "ledger": summary["ledger"],
                        "seed_counts": counts,
                        "ids": {
                            "dept_root": str(DEPT_ROOT),
                            "dept_mid": str(DEPT_MID),
                            "dept_leaf": str(DEPT_LEAF),
                            "role_dev": str(ROLE_DEV),
                            "user_a": USER_A,
                            "notice": NOTICE_PK,
                            "file": FILE_PK,
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        if args.keep:
            print(f"  · 临时工程保留在 {workdir}")
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
