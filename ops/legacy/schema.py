#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""存量库结构目录与差异引擎（真·原地升级的判定与计划来源）。

口径：把「旧库实际结构」与「当前代码的目标结构」各自拍平成
``{表: {列: 结构信息}}``，再做表级 / 列级差异；差异结果就是原地对齐计划的输入
（表改名 → 补列 → 其余按需处置）。设计边界：

- **只读**：本模块不做任何写入，计划（``build_plan``）是纯数据，执行由后续阶段承担；
- **两侧口径统一**：目标结构取自当前模型（``field.db_type``），旧库取自
  ``information_schema``，类型经 ``normalize_type`` 归一后比较，避免
  ``character varying(150)`` 与 ``varchar(150)`` 之类的伪差异；
- **框架基础设施表不参与**（``django_*`` / ``auth_*`` / ``celery_*`` 等前缀）：它们由各自的
  迁移维护，两侧一致，进了差异表只会淹没有效信息；
- **表改名优先按模型身份推断**：先命中 ``migration_ledger.PRE_SPLIT_TABLES`` 的显式映射，
  再用历史 tag 的迁移目录（``load_tag_models``）把旧表映射到模型名，最后按当前模型的
  「同 app 同名 / 跨 app 同名」推断；推断不出唯一目标的表进 ``blockers``，不猜。

用法（供 ``manage.py legacy_schema_report`` 与测试复用）::

    from ops.legacy import schema as legacy_schema

    report = legacy_schema.collect_report(dsn, tag="v4.2.5")
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from system.utils.platform.migration_ledger import PRE_SPLIT_TABLES

CATALOG_VERSION = 1

# 框架基础设施表前缀：两侧一致、不参与结构差异（与 ops/legacy_export.py 的二开表识别同源口径）
INFRA_TABLE_PREFIXES = (
    "django_",
    "auth_",
    "celery_",
    "django_celery_",
    "authtoken_",
    "socialaccount_",
    "social_django_",
)

# 旧版本（四域切分前）的 app 目录：样本库生成与历史目录解析共用
LEGACY_APPS = ("system", "notifications", "settings", "captcha", "common")

#: 统一人机验证 clean cut 后不再存在的旧表：升级时直接删除，不参与结构差异/阻断判定。
#: 旧图片码是一次性短命数据，删除不影响业务。
REMOVED_TABLES = frozenset({"captcha_captchastore"})

#: 单列结构信息：``{type, nullable, default}``（旧库）或 ``{type, sql_type, nullable, default_sql}``（目标侧）
ColumnInfo = dict[str, Any]

#: 表目录：``{表: {列: 列信息}}``，旧库与目标侧同形
TableCatalog = dict[str, dict[str, ColumnInfo]]

# 旧框架表前缀：落在这些前缀下却没命中映射的表按「需登记的框架表」处理（阻断项），
# 其他前缀视为二开表（原样保留，不进阻断项）
LEGACY_TABLE_PREFIXES = tuple(f"{app}_" for app in LEGACY_APPS)

# 类型归一：PG 的 information_schema 写法 → 短名（目标侧 field.db_type 已是短名）
_TYPE_ALIASES = (
    ("character varying", "varchar"),
    ("timestamp with time zone", "timestamptz"),
    ("timestamp without time zone", "timestamp"),
    ("time with time zone", "timetz"),
    ("time without time zone", "time"),
    ("double precision", "double"),
    ("integer", "int"),
    ("smallint", "smallint"),
    ("bigint", "bigint"),
)


def normalize_type(raw: str | None) -> str:
    """把 PG 类型串归一为可比较的短名（长度保留）。"""
    text = (raw or "").strip().lower()
    if not text:
        return ""
    for source, target in _TYPE_ALIASES:
        if text.startswith(source):
            text = target + text[len(source) :]
            break
    # 归一只为比较：`numeric(5, 1)` 与 `numeric(5,1)` 必须判等，因此去掉括号内的空格
    return re.sub(r"\s+", " ", text).replace(", ", ",").strip()


def _is_infra(table: str) -> bool:
    return table.startswith(INFRA_TABLE_PREFIXES)


# ---------------------------------------------------------------------------
# 目录采集
# ---------------------------------------------------------------------------


def introspect_schema(dsn: str, schema: str = "public") -> TableCatalog:
    """旧库/任意库的结构目录：``{表: {列: {type, nullable, default}}}``（只读）。"""
    import psycopg

    catalog: TableCatalog = {}
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "select table_name, column_name, data_type, udt_name, character_maximum_length,"
            " numeric_precision, numeric_scale, is_nullable, column_default"
            " from information_schema.columns where table_schema = %s"
            " order by table_name, ordinal_position",
            (schema,),
        )
        for row in cursor.fetchall():
            table, column, data_type, udt_name, char_len, num_prec, num_scale, nullable, default = row
            if _is_infra(table):
                continue
            # 自定义类型（pgvector 的 vector、citext 等）在 information_schema 里是 USER-DEFINED，
            # 真实类型名在 udt_name 上；不取它会把「同类型」误报成类型差异
            raw = udt_name if data_type == "USER-DEFINED" else data_type
            if char_len:
                raw = f"{data_type}({char_len})"
            elif num_prec and data_type in ("numeric", "decimal"):
                raw = f"{data_type}({num_prec},{num_scale})"
            catalog.setdefault(table, {})[column] = {
                "type": normalize_type(raw),
                "nullable": nullable == "YES",
                "default": (default or "").split("::")[0].strip() or None,
            }
    return catalog


def target_schema() -> TableCatalog:
    """当前代码的目标结构：``{表: {列: {type, nullable, default_sql}}}``（含自动 M2M 表）。

    ``default_sql`` 只对**可写成 SQL 字面量的默认值**给出（bool / 数字 / 字符串 / JSON）；
    可调用默认值（``default=dict`` 之类）与无默认都返回 ``None``——新增 NOT NULL 列时
    需要回填策略，执行器据此把它降级为人工复核项。
    """
    from django.apps import apps
    from django.db import connection

    catalog: TableCatalog = {}
    for model in apps.get_models(include_auto_created=True, include_swapped=True):
        table = model._meta.db_table
        if _is_infra(table):
            continue
        columns: dict[str, ColumnInfo] = {}
        for field in model._meta.local_fields:
            try:
                db_type = field.db_type(connection)
            except Exception:  # noqa: BLE001 - 个别字段需连接上下文：退化为字段类名，仍可比较
                db_type = field.get_internal_type()
            normalized = normalize_type(db_type)
            columns[field.column] = {
                "type": normalized,
                # DDL 用原始类型串：归一名（如 double）只服务比较，不能拿去建列
                "sql_type": (db_type or normalized).strip(),
                "nullable": bool(field.null),
                "default_sql": _literal_default(field, normalized),
            }
        catalog[table] = columns
    return catalog


def auto_created_tables() -> set[str]:
    """自动创建的 M2M / through 表集合（它们没有 ``django_content_type`` 行，不参与标签重指）。"""
    from django.apps import apps

    return {
        model._meta.db_table
        for model in apps.get_models(include_auto_created=True, include_swapped=True)
        if getattr(model._meta, "auto_created", False)
    }


def _literal_default(field: Any, column_type: str) -> str | None:
    """字段默认值 → SQL 字面量（不可写为字面量时返回 None）。"""
    from django.db.models import NOT_PROVIDED

    value = getattr(field, "default", NOT_PROVIDED)
    if value is NOT_PROVIDED or callable(value):
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    if isinstance(value, (list, dict)) and column_type.startswith("json"):
        escaped = json.dumps(value, ensure_ascii=False).replace("'", "''")
        return f"'{escaped}'::jsonb"
    return None


#: 历史模型目录：``{旧表: {app, model, tag}}``
TagModels = dict[str, dict[str, str]]


def load_tag_models(tag: str, repo_root: Path | None = None) -> TagModels:
    """从历史 tag 的迁移文件解析模型目录：``{旧表: {app, model, tag}}``（离线，AST）。

    只取 ``CreateModel`` 的名字与 ``db_table`` 选项：足以把旧表对应到模型身份，
    用于跨域改名推断（``system_userinfo`` → 模型 ``UserInfo`` → ``identity_userinfo``）。
    """
    root = repo_root or Path(__file__).resolve().parents[2]
    try:
        listing = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", tag],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return {}
    patterns = tuple(f"{app}/migrations/" for app in LEGACY_APPS)
    catalog: TagModels = {}
    for path in sorted(listing):
        if not path.endswith(".py") or "/migrations/" not in path or "__init__" in path:
            continue
        if not path.startswith(patterns):
            continue
        app = path.split("/migrations/")[0].split("/")[-1]
        source = subprocess.run(
            ["git", "show", f"{tag}:{path}"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for name, db_table in _parse_created_models(source):
            catalog[db_table or f"{app}_{name.lower()}"] = {"app": app, "model": name, "tag": tag}
    return catalog


def _parse_created_models(source: str) -> list[tuple[str, str]]:
    """AST 提取 ``CreateModel(name=..., options={'db_table': ...})``。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        attr = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if attr != "CreateModel":
            continue
        name = ""
        db_table = ""
        for keyword in node.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                name = str(keyword.value.value)
            elif keyword.arg == "options" and isinstance(keyword.value, ast.Dict):
                for key, value in zip(keyword.value.keys, keyword.value.values, strict=False):
                    if isinstance(key, ast.Constant) and key.value == "db_table" and isinstance(value, ast.Constant):
                        db_table = str(value.value)
        if name:
            found.append((name, db_table))
    return found


# ---------------------------------------------------------------------------
# 差异与计划
# ---------------------------------------------------------------------------


def _derive_from_renamed(table: str, renamed: dict[str, str], new: TableCatalog) -> str | None:
    """按「已确认的表改名」派生关联表改名：``system_userinfo_roles`` → ``identity_userinfo_roles``。

    自动 M2M / through 表不在 ``CreateModel`` 目录里（历史 AST 抓不到），但它们的名字由
    「被关联表名 + 字段段」拼成，因此父表改名确认后可直接替换前缀命中。
    """
    for source, target in renamed.items():
        if table.startswith(f"{source}_"):
            candidate = target + table[len(source) :]
            if candidate in new:
                return candidate
    return None


def _resolve_rename(
    table: str,
    new: TableCatalog,
    tag_models: TagModels,
    renamed: dict[str, str],
    new_by_suffix: dict[str, list[str]],
) -> str:
    """单表改名解析：显式映射 → 模型身份 → 关联表派生；返回空串表示暂时无解。"""
    target = dict(PRE_SPLIT_TABLES).get(table)
    if target and target in new:
        return target
    meta = (tag_models or {}).get(table)
    if meta:
        candidates = new_by_suffix.get(meta["model"].lower(), [])
        return candidates[0] if len(candidates) == 1 else ""
    return _derive_from_renamed(table, renamed, new) or ""


def _map_renamed(
    old: TableCatalog, new: TableCatalog, tag_models: TagModels
) -> tuple[dict[str, str], list[str], list[str]]:
    """旧表 → 新表的改名映射（同名天然对应，不进映射表）。

    推断顺序：显式映射（``PRE_SPLIT_TABLES``）→ 历史模型身份（AST 目录，按模型段唯一匹配）
    → 关联表派生（父表改名后替换前缀）。派生依赖前一轮结果，因此循环到不再有进展。

    仍未命中的表分两类：

    - 名字落在**框架旧前缀**（``system_`` / ``common_`` / ``notifications_`` / ``settings_`` /
      ``captcha_``）→ ``blockers``：它八成是被改过名/被删的框架表，不能瞎猜；
    - 其他前缀 → ``kept_unknown``：**二开表**（或历史临时表），按「原样保留」处理——PostgreSQL 的
      表改名会让二开表的外键自动跟随，因此它们不需要进映射表。把它们当阻断项会把所有二开库误判成
      「不可原地升级」。
    """
    renamed: dict[str, str] = {}
    new_by_suffix: dict[str, list[str]] = {}
    for table in new:
        new_by_suffix.setdefault(table.split("_", 1)[-1], []).append(table)
    pending = [table for table in sorted(old) if table not in new]
    while pending:
        resolved = {}
        for table in pending:
            target = _resolve_rename(table, new, tag_models, renamed, new_by_suffix)
            if target:
                resolved[table] = target
        if not resolved:
            break
        renamed.update(resolved)
        pending = [table for table in pending if table not in resolved]
    blockers, kept_unknown = [], []
    for table in pending:
        meta = (tag_models or {}).get(table)
        if meta:
            blockers.append(
                f"{table}: 历史模型 {meta['app']}.{meta['model']} 在新结构里对应"
                f" {len(new_by_suffix.get(meta['model'].lower(), []))} 张表，需人工指定映射"
            )
        elif table.startswith(LEGACY_TABLE_PREFIXES):
            blockers.append(f"{table}: 旧框架表在新结构中没有同名表，也无法从已确认的改名片派生，需在映射目录登记")
        else:
            kept_unknown.append(table)
    return renamed, blockers, kept_unknown


def diff_schemas(
    old: TableCatalog,
    new: TableCatalog,
    tag_models: TagModels | None = None,
) -> dict[str, Any]:
    """表级 / 列级差异（纯函数，供报告与执行计划复用）。"""
    old = {table: columns for table, columns in old.items() if table not in REMOVED_TABLES}
    renamed, blockers, kept_unknown = _map_renamed(old, new, tag_models or {})
    pairs = [(table, table) for table in sorted(old) if table in new]
    pairs += [(source, target) for source, target in sorted(renamed.items())]
    table_changes = []
    for source, target in pairs:
        old_columns, new_columns = old[source], new[target]
        added = sorted(set(new_columns) - set(old_columns))
        removed = sorted(set(old_columns) - set(new_columns))
        changed = sorted(
            column
            for column in set(old_columns) & set(new_columns)
            if old_columns[column]["type"] != new_columns[column]["type"]
        )
        if source != target or added or removed or changed:
            table_changes.append(
                {
                    "old": source,
                    "new": target,
                    "renamed": source != target,
                    "columns_added": added,
                    "columns_removed": removed,
                    "columns_type_changed": changed,
                }
            )
    kept = {target for _, target in pairs}
    return {
        "catalog_version": CATALOG_VERSION,
        "tables_old": len(old),
        "tables_new": len(new),
        "renamed": sorted(renamed.items()),
        "created_by_migrate": sorted(set(new) - kept),
        "unmatched_old": sorted(set(old) - set(new) - set(renamed)),
        "kept_unknown": kept_unknown,
        "changes": table_changes,
        "blockers": blockers,
    }


def build_plan(diff: dict[str, Any]) -> list[dict[str, Any]]:
    """把差异翻成有序计划项（纯数据；本阶段只用于 dry-run 与报告）。"""
    plan: list[dict[str, Any]] = []
    for source, target in diff["renamed"]:
        plan.append({"step": "rename_table", "from": source, "to": target})
    for change in diff["changes"]:
        for column in change["columns_added"]:
            plan.append({"step": "add_column", "table": change["new"], "column": column})
        for column in change["columns_type_changed"]:
            plan.append(
                {
                    "step": "alter_column_type",
                    "table": change["new"],
                    "column": column,
                    "review": True,
                }
            )
        for column in change["columns_removed"]:
            plan.append({"step": "drop_column", "table": change["new"], "column": column, "review": True})
    return plan


def collect_report(dsn: str, tag: str | None = None) -> dict[str, Any]:
    """汇总报告：旧库结构（``dsn``）↔ 目标结构，附差异、计划与阻断项。"""
    old = introspect_schema(dsn)
    new = target_schema()
    tag_models = load_tag_models(tag) if tag else {}
    diff = diff_schemas(old, new, tag_models)
    notes = []
    if tag and not tag_models:
        # 部署环境（容器镜像）里没有 .git：按 tag 推断模型归属这条路取不到历史，
        # 此时只有 PRE_SPLIT_TABLES 登记表可信，必须显式说明，避免把「登记缺失」误读成结构问题
        notes.append(
            f"未能从 {tag} 加载历史模型目录（当前环境没有 git 历史）：表改名只依赖 "
            f"migration_ledger.PRE_SPLIT_TABLES 登记表；出现未映射的框架表请在该表补登记"
        )
    diff["notes"] = notes
    diff["plan"] = build_plan(diff)
    diff["ready"] = not diff["blockers"]
    return diff
