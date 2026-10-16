#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""迁移账本认领：4.x 分散迁移链 ↔ 合并链的对账与认领（幂等，只写迁移记录）。

5.0 起各业务 app 的迁移链合并为初始迁移（``0001_initial`` / ``0002_initial``），
与它在表结构上等价的存量库可以「认领」账本（只改迁移记录，不执行 DDL）。等价前提是
**四域切分之后**的 4.x 链：用户 / 文件 / 日志 / 任务已各自成域（``identity_*`` /
``file_*`` / ``audit_*`` / ``task_*`` 表名），5.0 的合并链只改变迁移文件的组织方式。

**四域切分之前**的库（表名仍是 ``system_userinfo`` 等，字段集也更少）不在等价范围内：
认领只写迁移记录，既不会重命名表、也不会补齐列，强行认领会得到一个「记录看着对、
结构不对」的库。这类库由 ``PRE_SPLIT_TABLES`` 精确识别并拒绝认领，改为走
「备份 + 清库重建 + ``legacy_export`` / ``legacy_import`` 回灌」。

本模块在**表结构校验通过**的前提下完成认领：

1. 校验：不属于四域切分前的旧库，游离记录全部命中等价链白名单（``LEGACY_4X_CHAIN``），
   且表结构无缺表/缺列；
2. 认领：删除游离记录，并把同 app 待应用的合并链迁移记为已应用（不执行 DDL）。

全新库（迁移记录即合并链）与已认领的库均为 no-op；白名单之外的游离记录（未知链路）
直接拒绝，不做任何写入。
"""

from typing import Any

# 等价链白名单：app → 该 app 在「四域切分之后、5.0 合并之前」的 4.x 链中的全部迁移名。
# 合并链的初始迁移与它在表结构上等价，因此这些记录可被安全认领（删除）。
LEGACY_4X_CHAIN: dict[str, tuple[str, ...]] = {
    "ai": ("0001_initial",),
    "approval": ("0001_initial", "0002_approvalrule_methods"),
    "audit": ("0001_initial",),
    "captcha": ("0001_initial",),
    "dataset": (
        "0001_initial",
        "0002_screen_free_layout",
        "0003_screentemplate",
        "0004_screen_template_group_versions_clipboard",
        "0005_alter_screenclipboard_options_and_more",
    ),
    "demo": ("0001_initial",),
    "file": ("0001_initial",),
    "identity": ("0001_initial", "0002_deptinfo_rules_userinfo_rules_userrole_menu"),
    "message": ("0001_initial",),
    "mfa": ("0001_initial",),
    "notifications": ("0001_initial",),
    "settings": ("0001_initial",),
    "system": (
        "0001_initial",
        "0002_monitor_monitoralert",
        "0003_codegenplan",
        "0004_alter_userpersonalconfig_options",
        "0005_menumeta_active_path_menumeta_badge_and_more",
    ),
    "task": ("0001_initial", "0002_webhookdelivery_generation", "0003_periodictaskowner"),
}

# 四域切分（用户 / 文件 / 日志 / 任务各自的 app 与表名）之前的表名 → 当前表名。
# 旧表在、当前表不在，即证明该库仍停留在切分前的结构：表名与列集都与合并链不一致，
# 认领（只写迁移记录）无法把它变成 5.0 的库，必须走「清库重建 + 回灌」。
PRE_SPLIT_TABLES: tuple[tuple[str, str], ...] = (
    ("system_userinfo", "identity_userinfo"),
    ("system_deptinfo", "identity_deptinfo"),
    ("system_userrole", "identity_userrole"),
    ("system_uploadfile", "file_uploadfile"),
    ("system_operationlog", "audit_operationlog"),
    ("system_userloginlog", "audit_userloginlog"),
)

# 认领后的判定：consistent（无需动作）/ reconcilable（可认领）/ unsupported（未知链路，人工介入）
VERDICTS = {
    "consistent": "迁移记录与当前迁移链一致，无需认领。",
    "reconcilable": "库来自 4.x 迁移链且表结构一致，可认领迁移账本（不执行 DDL）。",
    "unsupported": "存在非 4.x 迁移链的游离记录或表结构漂移，不支持自动认领，请人工介入。",
}


class LedgerError(RuntimeError):
    """认领前置校验失败（未知链路 / 表结构漂移）。"""


def legacy_chain_records() -> set[tuple[str, str]]:
    """4.2.5 全链的 (app_label, migration_name) 集合。"""
    return {(app, name) for app, names in LEGACY_4X_CHAIN.items() for name in names}


def owned_apps() -> set[str]:
    """参与合并链认领的业务 app。"""
    return set(LEGACY_4X_CHAIN)


def collect_migration_ledger(connection: Any) -> dict[str, Any]:
    """迁移记录对账：已应用 / 待应用 / 游离（区分白名单内与未知链路）。"""
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(connection, ignore_no_migrations=True)
    applied = set((loader.applied_migrations or {}).keys())
    replaced = set(loader.replacements.keys())
    expected = {key for key in loader.graph.nodes if key not in replaced}
    orphan = applied - expected
    pending = expected - applied
    known = legacy_chain_records()
    apps = owned_apps()
    legacy_orphan = sorted(f"{app}.{name}" for app, name in orphan & known)
    unknown_orphan = sorted(f"{app}.{name}" for app, name in orphan - known)
    applied_apps = {app for app, _ in applied}
    claimable = sorted(f"{app}.{name}" for app, name in pending if app in apps and app in applied_apps)
    return {
        "applied": len(applied),
        "pending": sorted(f"{app}.{name}" for app, name in pending),
        "claimable": claimable,
        # 兼容字段：游离记录全集（旧版消费者只看 orphan 时仍可工作）
        "orphan": sorted(set(legacy_orphan) | set(unknown_orphan)),
        "legacy_orphan": legacy_orphan,
        "unknown_orphan": unknown_orphan,
    }


def collect_schema_ledger(connection: Any) -> dict[str, Any]:
    """表结构体检：按模型列举的表/列与真实库比对（向量列随扩展可选，不参与判定）。"""
    from django.apps import apps

    models = [model for model in apps.get_models() if getattr(model._meta, "managed", True)]
    tables: dict[str, Any] = {}
    for model in models:
        tables.setdefault(model._meta.db_table, model)
    with connection.cursor() as cursor:
        existing = set(connection.introspection.table_names(cursor))
    missing_tables = sorted(name for name in tables if name not in existing)
    pre_split_tables = [
        {"legacy": legacy, "current": current}
        for legacy, current in PRE_SPLIT_TABLES
        if legacy in existing and current not in existing
    ]
    missing_columns = []
    for name, model in tables.items():
        if name not in existing:
            continue
        with connection.cursor() as cursor:
            description = connection.introspection.get_table_description(cursor, name)
        columns = {str(getattr(column, "name", "")).lower() for column in description}
        expected_columns = {
            str(field.column).lower()
            for field in model._meta.local_fields
            if getattr(field, "concrete", False)
            and getattr(field, "column", None)
            and field.__class__.__name__ != "VectorField"
        }
        absent = sorted(expected_columns - columns)
        if absent:
            missing_columns.append({"table": name, "columns": absent})
    return {
        "tables_checked": len(tables),
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "pre_split_tables": pre_split_tables,
    }


def collect_ledger(connection: Any) -> dict[str, Any]:
    """对账报告（只读）。"""
    return {"migrations": collect_migration_ledger(connection), "schema": collect_schema_ledger(connection)}


def evaluate_ledger(ledger: dict[str, Any]) -> str:
    """按对账报告判定认领可行性（纯函数，便于单测覆盖各分支）。

    判定依据是「4.x 游离记录存在」——只有 4.x 链才会留下这些迁移名，因此它的出现
    即证明库来自旧链（此时同 app 的合并链待应用迁移同样已被旧链覆盖，可一并补记）；
    仅「待应用迁移」而无游离记录属正常待迁移（例如合并链自身中途失败），不认领。
    """
    migrations, schema = ledger["migrations"], ledger["schema"]
    if schema.get("pre_split_tables"):
        return "unsupported"
    if migrations["unknown_orphan"]:
        return "unsupported"
    if schema["missing_tables"] or schema["missing_columns"]:
        return "unsupported"
    if migrations["legacy_orphan"]:
        return "reconcilable"
    return "consistent"


def reconcile(connection: Any) -> dict[str, Any]:
    """执行认领：删除 4.x 游离记录 + 补记合并链迁移（不执行 DDL）。

    前置：``evaluate_ledger`` 判定为 ``consistent`` 或 ``reconcilable``；
    ``unsupported`` 抛出 ``LedgerError``（调用方决定退出码）。
    """
    from django.db import transaction
    from django.db.migrations.recorder import MigrationRecorder

    ledger = collect_ledger(connection)
    verdict = evaluate_ledger(ledger)
    if verdict == "unsupported":
        pre_split = ledger["schema"].get("pre_split_tables") or []
        raise LedgerError(
            "迁移账本认领前置校验失败："
            f"四域切分前的旧表 {[item['legacy'] for item in pre_split][:5]}，"
            f"未知链路游离记录 {ledger['migrations']['unknown_orphan'][:5]}，"
            f"缺表 {ledger['schema']['missing_tables'][:5]}，"
            f"缺列 {[item['table'] for item in ledger['schema']['missing_columns']][:5]}"
        )
    if verdict == "consistent":
        return {"verdict": verdict, "removed": [], "recorded": []}

    removed: list[str] = []
    recorded: list[str] = []
    recorder = MigrationRecorder(connection)
    with transaction.atomic(using=connection.alias):
        for record in ledger["migrations"]["legacy_orphan"]:
            app, name = record.split(".", 1)
            recorder.migration_qs.filter(app=app, name=name).delete()
            removed.append(record)
        for record in ledger["migrations"]["claimable"]:
            app, name = record.split(".", 1)
            recorder.record_applied(app, name)
            recorded.append(record)
    return {"verdict": verdict, "removed": removed, "recorded": recorded}
