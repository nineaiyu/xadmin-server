# -*- coding: utf-8 -*-
"""旧结构样本库与结构差异报告的端到端守护（真·原地升级的验证靶）。

用历史 tag 的迁移在测试 PG 上真实建一个「四域切分之前」的库（``ops/legacy_sample_db.py``），
再对它有断言：旧表名与旧主键类型、旧迁移账本、结构差异报告可自动对齐（无阻断项）、
以及样本数据的主键/UUID 基线（结构对齐必须原样保留这些值）。

单仓检出（没有历史 tag）时跳过：本保障依赖 git 历史。
"""

import uuid

import pytest

from system.utils.platform import legacy_schema


class TestLegacySampleStructure:
    def test_legacy_tables_and_primary_key_types(self, legacy_sample):
        catalog = legacy_schema.introspect_schema(legacy_sample[0])
        # 四域切分前的表名与当时的真实主键类型（UUID 域与自增域混用）
        for table in ("system_userinfo", "system_deptinfo", "system_userrole", "system_uploadfile"):
            assert table in catalog
        assert catalog["system_userinfo"]["id"]["type"] == "bigint"
        assert catalog["system_deptinfo"]["id"]["type"] == "uuid"
        assert catalog["system_menu"]["id"]["type"] == "uuid"
        # 结构差异的驱动列：旧库这两处还没有 deleted_at（切分后新增）
        assert "deleted_at" not in catalog["system_menu"]
        assert "deleted_at" not in catalog["notifications_messagecontent"]

    def test_legacy_migration_ledger_is_recorded(self, legacy_sample):
        import psycopg

        with psycopg.connect(legacy_sample[0]) as connection, connection.cursor() as cursor:
            cursor.execute(
                "select app, count(*) from django_migrations where app in"
                " ('system', 'notifications', 'settings', 'captcha', 'common') group by app"
            )
            ledger = dict(cursor.fetchall())
        # 旧链的节点数就是「同名迁移不同内容」问题的来源：这些名字在新链里含义已变
        assert ledger == {"captcha": 1, "common": 1, "notifications": 2, "settings": 2, "system": 3}

    def test_seed_data_ids_are_the_preservation_baseline(self, legacy_sample):
        import psycopg

        meta = legacy_sample[1]
        with psycopg.connect(legacy_sample[0]) as connection, connection.cursor() as cursor:
            cursor.execute("select username, dept_id from system_userinfo order by id")
            users = cursor.fetchall()
            cursor.execute("select code, is_active from system_userrole order by code")
            roles = cursor.fetchall()
            cursor.execute("select count(*) from notifications_messageuserread")
            reads = cursor.fetchone()[0]
        assert users == [
            ("legacy_sample_a", uuid.UUID(meta["ids"]["dept_leaf"])),
            ("legacy_sample_b", uuid.UUID(meta["ids"]["dept_mid"])),
        ]
        assert roles == [("R_SAMPLE_DEV", True), ("R_SAMPLE_QA", False)]
        assert reads == 2


class TestStructureReport:
    def test_report_is_auto_alignable(self, legacy_sample):
        report = legacy_schema.collect_report(legacy_sample[0], tag=legacy_sample[1]["tag"])
        assert report["blockers"] == []
        assert report["ready"] is True
        assert report["tables_old"] > 20 and report["tables_new"] > 90

    def test_explicit_and_derived_renames_are_planned(self, legacy_sample):
        report = legacy_schema.collect_report(legacy_sample[0], tag=legacy_sample[1]["tag"])
        renamed = dict(report["renamed"])
        for source, target in (
            ("system_userinfo", "identity_userinfo"),
            ("system_deptinfo", "identity_deptinfo"),
            ("system_userrole", "identity_userrole"),
            ("system_uploadfile", "file_uploadfile"),
            ("system_operationlog", "audit_operationlog"),
            ("system_userloginlog", "audit_userloginlog"),
        ):
            assert renamed.get(source) == target
        # 关联表改名由父表派生（历史 CreateModel 目录里没有自动 M2M 表）
        assert renamed.get("system_userinfo_roles") == "identity_userinfo_roles"
        assert renamed.get("system_userrole_menu") == "identity_userrole_menu"

    def test_column_gaps_match_the_target(self, legacy_sample):
        report = legacy_schema.collect_report(legacy_sample[0], tag=legacy_sample[1]["tag"])
        changes = {item["new"]: item for item in report["changes"]}
        assert "deleted_at" in changes["system_menu"]["columns_added"]
        assert "deleted_at" in changes["notifications_messagecontent"]["columns_added"]
        assert {"active_path", "badge", "query"} <= set(changes["system_menumeta"]["columns_added"])

    def test_plan_has_renames_then_column_steps(self, legacy_sample):
        report = legacy_schema.collect_report(legacy_sample[0], tag=legacy_sample[1]["tag"])
        steps = [item["step"] for item in report["plan"]]
        assert steps.count("rename_table") == len(report["renamed"])
        assert "add_column" in steps
        assert steps.index("rename_table") < steps.index("add_column")


class TestTargetSchemaGuards:
    def test_target_schema_carries_sql_type_for_ddl(self):
        # 归一名（double / timestamptz）只服务比较，DDL 必须用原始类型串
        columns = legacy_schema.target_schema()["system_monitor"]
        numeric = [item for item in columns.values() if item["sql_type"].startswith("double precision")]
        assert numeric and any(item["type"] == "double" for item in numeric)

    def test_non_nullable_columns_without_literal_default_are_marked(self):
        columns = legacy_schema.target_schema()["identity_userinfo"]
        assert columns["allowed_mfa_types"]["nullable"] is False
        assert columns["allowed_mfa_types"]["default_sql"] is None
        assert columns["must_change_password"]["default_sql"] == "false"

    def test_infra_tables_are_excluded(self):
        catalog = legacy_schema.target_schema()
        assert all(not table.startswith(("auth_", "django_", "celery_")) for table in catalog)


@pytest.mark.parametrize("table", ["identity_userinfo_roles", "identity_userrole_menu"])
def test_derived_join_tables_exist_in_target(table, legacy_sample):
    assert table in legacy_schema.target_schema()
