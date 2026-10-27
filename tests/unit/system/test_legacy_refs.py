# -*- coding: utf-8 -*-
"""原地升级「引用层」与前置操作白名单的守护。

三类风险：
1. 标签迁移推导错（把自动 M2M 表也算成模型迁移）→ 内容类型重指指向不存在的行；
2. 存量字符串改写口径错（子串误伤 / 漏改前缀形态）→ 升级后字段树、数据权限指向不存在的模型；
3. 迁移链新增 ``RunPython`` 未登记却被结构对齐路径执行 → 数据回填类操作在旧库上盲目运行。
"""

from django.db.migrations.loader import MigrationLoader

from ops.legacy import align as legacy_align
from ops.legacy import refs as legacy_refs
from ops.legacy import structure as legacy_structure


class TestContentTypeMoves:
    RENAMED = [
        ("system_userinfo", "identity_userinfo"),
        ("system_userinfo_roles", "identity_userinfo_roles"),
        ("common_monitor", "system_monitor"),
        ("system_menu", "system_menu"),
    ]

    def test_derives_label_moves_and_skips_auto_tables(self):
        auto = {"identity_userinfo_roles"}
        moves = legacy_refs.content_type_moves(self.RENAMED, auto)
        assert moves == {"system.userinfo": "identity.userinfo", "common.monitor": "system.monitor"}

    def test_same_table_name_produces_no_move(self):
        assert legacy_refs.content_type_moves([("system_menu", "system_menu")], set()) == {}

    def test_auto_table_without_rename_is_ignored(self):
        moves = legacy_refs.content_type_moves([("identity_userinfo_roles", "identity_userinfo_roles")], set())
        assert moves == {}


class TestLabelRewrite:
    MOVES = {"system.userinfo": "identity.userinfo", "common.monitor": "system.monitor"}

    def test_exact_and_prefixed_labels_are_rewritten(self):
        assert legacy_refs._rewrite_label_value("system.userinfo", self.MOVES) == "identity.userinfo"
        assert legacy_refs._rewrite_label_value("system.userinfo.username", self.MOVES) == "identity.userinfo.username"

    def test_other_labels_are_untouched(self):
        assert legacy_refs._rewrite_label_value("system.menu", self.MOVES) == "system.menu"
        assert legacy_refs._rewrite_label_value("system.userinfo", {}) == "system.userinfo"

    def test_no_substring_damage(self):
        # 前缀替换必须锚定分段边界：system.userinfo2 不是 system.userinfo 的引用
        assert legacy_refs._rewrite_label_value("system.userinfo2", self.MOVES) == "system.userinfo2"

    def test_label_pattern_anchors_full_value(self):
        pattern = legacy_refs._label_pattern("system.userinfo")
        import re

        assert re.match(pattern, "system.userinfo")
        assert re.match(pattern, "system.userinfo.username")
        assert not re.match(pattern, "system.userinfo2")


class TestPrerequisiteAllowlist:
    """前置 RunPython 白名单必须覆盖迁移链（否则结构对齐会静默漏掉扩展创建）。"""

    def test_every_project_run_python_in_chain_is_registered(self):
        """本仓库 app 的 ``RunPython`` 必须明确「执行 / 建表后执行 / 明确跳过」之一。"""
        from django.db.migrations.operations.special import RunPython

        loader = MigrationLoader(None, ignore_no_migrations=False)
        unregistered = []
        for (app, name), migration in loader.disk_migrations.items():
            if not legacy_align.is_project_migration(migration):
                continue
            for operation in migration.operations:
                if isinstance(operation, RunPython) and operation.code is not None:
                    function_name = getattr(operation.code, "__name__", "")
                    registered = {
                        *legacy_structure.PREREQUISITE_CALLABLES,
                        *legacy_structure.DATA_CLEANUP_CALLABLES,
                        *legacy_structure.DEFERRED_CALLABLES,
                    }
                    if function_name not in registered:
                        unregistered.append(f"{app}.{name}:{function_name}")
        assert unregistered == [], (
            "本仓库迁移链出现未登记的前置 RunPython：结构对齐路径不会执行它（新增数据回填类操作时"
            f"必须明确决策是「执行」还是「保持跳过」并更新 PREREQUISITE_CALLABLES）：{unregistered}"
        )

    def test_no_project_run_sql_in_chain(self):
        from django.db.migrations.operations.special import RunSQL

        loader = MigrationLoader(None, ignore_no_migrations=False)
        found = [
            f"{app}.{name}"
            for (app, name), migration in loader.disk_migrations.items()
            if legacy_align.is_project_migration(migration)
            for operation in migration.operations
            if isinstance(operation, RunSQL)
        ]
        # RunSQL 一律不执行（无法判断是否幂等）：出现即需人工决策，所以这里守住「链上没有」
        assert found == []

    def test_project_and_third_party_are_distinguished(self):
        loader = MigrationLoader(None, ignore_no_migrations=False)
        project = {
            app
            for (app, _name), migration in loader.disk_migrations.items()
            if legacy_align.is_project_migration(migration)
        }
        assert {"system", "identity", "file", "audit", "ai", "dataset"} <= project
        assert not {"auth", "contenttypes", "django_celery_results", "token_blacklist"} & project


class TestStoredRefRegistry:
    def test_registry_entries_are_well_formed(self):
        for target in legacy_refs.STORED_REF_TARGETS:
            assert target["mode"] in ("label", "label_json")
            assert target["table"] and target["column"]
            if target["mode"] == "label_json":
                assert target.get("keys")

    def test_registry_covers_documented_targets(self):
        keys = {f"{item['table']}.{item['column']}" for item in legacy_refs.STORED_REF_TARGETS}
        assert {
            "system_modellabelfield.name",
            "system_datapermission.rules",
            "audit_datamaskrule.model",
            "task_importtemplate.model",
        } <= keys
