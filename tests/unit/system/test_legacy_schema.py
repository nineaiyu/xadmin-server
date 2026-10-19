# -*- coding: utf-8 -*-
"""存量库结构目录与差异引擎守护（原地升级的判定与计划来源）。

覆盖四类风险：
1. 类型归一（``character varying(150)`` ↔ ``varchar(150)``）失效 → 差异表被伪差异淹没；
2. 改名推断规则退化（显式映射 / 历史模型身份 / 关联表派生）→ 计划漏改名或误改名；
3. 差异与计划结构漂移（补列、类型变更、需人工复核项）→ 执行阶段拿不到稳定输入；
4. 目标结构采集口径（排除框架基础设施表、含自动 M2M 表）变化 → 报告面错误。
"""

from system.utils.platform import legacy_schema


def _column(type_name: str, nullable: bool = True) -> dict:
    return {"type": type_name, "nullable": nullable}


class TestNormalizeTypeSpacing:
    def test_numeric_spacing_and_time_variants(self):
        assert legacy_schema.normalize_type("numeric(5, 1)") == legacy_schema.normalize_type("numeric(5,1)")
        assert legacy_schema.normalize_type("time without time zone") == "time"
        assert legacy_schema.normalize_type("time with time zone") == "timetz"


class TestNormalizeType:
    def test_spacing_and_time_variants_are_normalized(self):
        # 归一必须吃掉括号内空格与 time 变体，否则会产出伪类型差异
        assert legacy_schema.normalize_type("numeric(5, 1)") == legacy_schema.normalize_type("numeric(5,1)")
        assert legacy_schema.normalize_type("time without time zone") == "time"
        assert legacy_schema.normalize_type("time with time zone") == "timetz"

    def test_pg_long_forms_are_shortened(self):
        assert legacy_schema.normalize_type("character varying(150)") == "varchar(150)"
        assert legacy_schema.normalize_type("timestamp with time zone") == "timestamptz"
        assert legacy_schema.normalize_type("timestamp without time zone") == "timestamp"
        assert legacy_schema.normalize_type("integer") == "int"
        assert legacy_schema.normalize_type("double precision") == "double"

    def test_short_forms_and_empty_values_pass_through(self):
        assert legacy_schema.normalize_type("varchar(64)") == "varchar(64)"
        assert legacy_schema.normalize_type("uuid") == "uuid"
        assert legacy_schema.normalize_type(None) == ""
        assert legacy_schema.normalize_type("  BIGINT ") == "bigint"


class TestParseCreatedModels:
    SOURCE = """
class Migration(migrations.Migration):
    operations = [
        migrations.CreateModel(
            name='UserInfo',
            fields=[('id', models.BigAutoField(primary_key=True))],
            options={'db_table': 'system_userinfo', 'verbose_name': 'User'},
        ),
        migrations.CreateModel(
            name='UserRole',
            fields=[('id', models.UUIDField(primary_key=True))],
        ),
    ]
"""

    def test_parses_name_and_db_table(self):
        assert legacy_schema._parse_created_models(self.SOURCE) == [
            ("UserInfo", "system_userinfo"),
            ("UserRole", ""),
        ]

    def test_invalid_source_is_ignored(self):
        assert legacy_schema._parse_created_models("def broken(:\n") == []


class TestRenameMapping:
    OLD = {
        "system_userinfo": {},
        "system_userinfo_roles": {},
        "system_userrole": {},
        "system_userrole_menu": {},
    }
    NEW = {
        "identity_userinfo": {},
        "identity_userinfo_roles": {},
        "identity_userrole": {},
        "identity_userrole_menu": {},
    }

    def test_explicit_ledger_mapping_is_used(self):
        renamed, blockers, kept = legacy_schema._map_renamed(self.OLD, self.NEW, {})
        assert renamed["system_userinfo"] == "identity_userinfo"
        assert blockers == []

    def test_related_tables_are_derived_from_renamed_parent(self):
        # 自动 M2M 表不在历史 CreateModel 目录里：父表改名确认后按前缀派生
        renamed, blockers, kept = legacy_schema._map_renamed(self.OLD, self.NEW, {})
        assert renamed["system_userinfo_roles"] == "identity_userinfo_roles"
        assert renamed["system_userrole_menu"] == "identity_userrole_menu"
        assert blockers == []

    def test_historical_model_identity_is_used_when_not_explicit(self):
        old = {"common_monitor": {}}
        new = {"system_monitor": {}}
        renamed, blockers, kept = legacy_schema._map_renamed(
            old, new, {"common_monitor": {"app": "common", "model": "Monitor"}}
        )
        assert renamed == {"common_monitor": "system_monitor"}
        assert blockers == []

    def test_ambiguous_model_reports_blocker(self):
        old = {"system_messagecontent": {}}
        new = {"audit_messagecontent": {}, "notifications_messagecontent": {}}
        renamed, blockers, kept = legacy_schema._map_renamed(old, new, {})
        assert renamed == {}
        assert len(blockers) == 1 and "无法从已确认的改名片派生" in blockers[0]

    def test_unknown_legacy_framework_table_reports_blocker(self):
        # 旧框架前缀下却没命中映射：不瞎猜，交人工登记
        renamed, blockers, kept = legacy_schema._map_renamed(
            {"system_scratch_table": {}}, {"identity_userinfo": {}}, {}
        )
        assert renamed == {}
        assert kept == []
        assert len(blockers) == 1 and "system_scratch_table" in blockers[0]

    def test_third_party_tables_are_kept_not_blockers(self):
        # 二开表（非框架前缀）原样保留：PG 表改名会让它们的外键自动跟随，不该阻断原地升级
        old = {"system_userinfo": {}, "crm_customer": {}, "shop_order_item": {}}
        new = {"identity_userinfo": {}}
        renamed, blockers, kept = legacy_schema._map_renamed(old, new, {})
        assert renamed == {"system_userinfo": "identity_userinfo"}
        assert blockers == []
        assert kept == ["crm_customer", "shop_order_item"]


class TestDiffAndPlan:
    OLD = {
        "system_menu": {"id": _column("uuid", False), "rank": _column("int", False)},
        "system_userinfo": {"id": _column("bigint", False)},
    }
    NEW = {
        "system_menu": {
            "id": _column("uuid", False),
            "rank": _column("int", False),
            "deleted_at": _column("timestamptz"),
        },
        "identity_userinfo": {"id": _column("bigint", False), "nickname": _column("varchar(150)", False)},
        "identity_userrole": {"id": _column("uuid", False)},
    }

    def test_reports_renames_added_columns_and_created_tables(self):
        diff = legacy_schema.diff_schemas(self.OLD, self.NEW, {})
        assert diff["renamed"] == [("system_userinfo", "identity_userinfo")]
        assert diff["created_by_migrate"] == ["identity_userrole"]
        assert diff["unmatched_old"] == []
        assert diff["kept_unknown"] == []
        assert diff["kept_unknown"] == []
        changes = {item["new"]: item for item in diff["changes"]}
        assert changes["system_menu"]["columns_added"] == ["deleted_at"]
        assert changes["identity_userinfo"]["columns_added"] == ["nickname"]
        assert diff["blockers"] == []

    def test_type_changes_are_surfaced(self):
        old = {"system_menu": {"rank": _column("int")}}
        new = {"system_menu": {"rank": _column("bigint")}}
        diff = legacy_schema.diff_schemas(old, new, {})
        assert diff["changes"][0]["columns_type_changed"] == ["rank"]

    def test_plan_renames_before_column_work(self):
        diff = legacy_schema.diff_schemas(self.OLD, self.NEW, {})
        plan = legacy_schema.build_plan(diff)
        steps = [item["step"] for item in plan]
        assert steps.index("rename_table") < steps.index("add_column")
        assert {"step": "rename_table", "from": "system_userinfo", "to": "identity_userinfo"} in plan
        assert {"step": "add_column", "table": "system_menu", "column": "deleted_at"} in plan

    def test_review_steps_are_marked(self):
        diff = legacy_schema.diff_schemas(
            {"system_menu": {"gone": _column("int")}},
            {"system_menu": {"gone": _column("bigint")}},
            {},
        )
        plan = legacy_schema.build_plan(diff)
        assert len(plan) == 1 and plan[0]["step"] == "alter_column_type" and plan[0]["review"] is True


class TestTargetSchema:
    def test_target_schema_uses_current_models(self):
        catalog = legacy_schema.target_schema()
        # 四域切分后的表名（改名目标）与自动 M2M 表（关联表改名的目标）都在册
        assert "identity_userinfo" in catalog
        assert "identity_user_info_roles" not in catalog  # 名字口径守护：不该出现臆造表
        assert "identity_userinfo_roles" in catalog
        assert "file_uploadfile" in catalog

    def test_infra_tables_are_excluded(self):
        catalog = legacy_schema.target_schema()
        assert "django_migrations" not in catalog
        assert all(not table.startswith(("auth_", "django_", "celery_")) for table in catalog)

    def test_userinfo_key_columns_present(self):
        columns = legacy_schema.target_schema()["identity_userinfo"]
        for column in ("id", "username", "password", "dept_id", "creator_id"):
            assert column in columns


class TestShippedRegistryIsSelfSufficient:
    """部署环境（容器镜像）没有 .git：按 tag 推断那条路取不到历史，登记表必须能独立完成改名。

    真实 4.2.5 库演练就是靠这条发现 ``common_monitor`` 漏登记：容器里 git 历史缺失，
    该表的改名推断失败，原地升级被判阻断（fail-closed 生效，但登记表不应缺项）。
    """

    # 四域切分前的框架表（含关联表）：全部应能只靠登记表 + 派生规则完成映射
    LEGACY_TABLES = (
        "system_userinfo",
        "system_userinfo_roles",
        "system_userinfo_rules",
        "system_userinfo_groups",
        "system_userinfo_user_permissions",
        "system_deptinfo",
        "system_deptinfo_roles",
        "system_deptinfo_rules",
        "system_userrole",
        "system_userrole_menu",
        "system_uploadfile",
        "system_operationlog",
        "system_userloginlog",
        "common_monitor",
    )

    def test_registry_has_the_framework_moves(self):
        from system.utils.platform.migration_ledger import PRE_SPLIT_TABLES

        registry = dict(PRE_SPLIT_TABLES)
        for legacy, current in (
            ("system_userinfo", "identity_userinfo"),
            ("system_deptinfo", "identity_deptinfo"),
            ("system_userrole", "identity_userrole"),
            ("system_uploadfile", "file_uploadfile"),
            ("system_operationlog", "audit_operationlog"),
            ("system_userloginlog", "audit_userloginlog"),
            ("common_monitor", "system_monitor"),  # Monitor 从 common 域搬到 system 域
        ):
            assert registry.get(legacy) == current, f"登记表缺 {legacy} → {current}"

    def test_all_framework_tables_map_without_tag_catalog(self):
        target = legacy_schema.target_schema()
        old = {table: {} for table in self.LEGACY_TABLES}
        renamed, blockers, kept = legacy_schema._map_renamed(old, target, {})
        assert blockers == [], f"没有 tag 目录时必须靠登记表兜底，未映射：{blockers}"
        assert len(renamed) == len(self.LEGACY_TABLES)
        assert kept == []
