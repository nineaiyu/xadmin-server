# -*- coding: utf-8 -*-
"""迁移账本认领守护：判定分支 + 真实库对账 + 认领写入（幂等）。"""

import json
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder

from system.utils.platform.migration_ledger import (
    LEGACY_4X_CHAIN,
    PRE_SPLIT_TABLES,
    LedgerError,
    collect_ledger,
    collect_schema_ledger,
    evaluate_ledger,
    legacy_chain_records,
    reconcile,
)


def _ledger(legacy_orphan=(), unknown_orphan=(), missing_tables=(), missing_columns=(), claimable=(), pre_split=()):
    return {
        "migrations": {
            "applied": 1,
            "pending": [],
            "claimable": list(claimable),
            "orphan": sorted(set(legacy_orphan) | set(unknown_orphan)),
            "legacy_orphan": list(legacy_orphan),
            "unknown_orphan": list(unknown_orphan),
        },
        "schema": {
            "tables_checked": 1,
            "missing_tables": list(missing_tables),
            "missing_columns": list(missing_columns),
            "pre_split_tables": list(pre_split),
        },
    }


class TestEvaluateLedger:
    def test_consistent_without_orphan(self):
        assert evaluate_ledger(_ledger()) == "consistent"

    def test_legacy_orphan_is_reconcilable(self):
        ledger = _ledger(legacy_orphan=["system.0002_monitor_monitoralert"], claimable=["system.0002_initial"])
        assert evaluate_ledger(ledger) == "reconcilable"

    def test_unknown_orphan_is_unsupported(self):
        assert evaluate_ledger(_ledger(unknown_orphan=["legacy_app.0001_initial"])) == "unsupported"

    def test_schema_drift_blocks_reconcile(self):
        ledger = _ledger(legacy_orphan=["system.0002_monitor_monitoralert"], missing_tables=["system_monitor"])
        assert evaluate_ledger(ledger) == "unsupported"

    def test_pre_split_database_is_unsupported(self):
        """四域切分前的旧库不得认领：认领只写迁移记录，无法改表名与补列。"""
        ledger = _ledger(
            legacy_orphan=["system.0002_monitor_monitoralert"],
            pre_split=[{"legacy": "system_userinfo", "current": "identity_userinfo"}],
        )
        assert evaluate_ledger(ledger) == "unsupported"

    def test_legacy_whitelist_covers_every_business_app(self):
        """白名单必须覆盖全部迁移合并过的业务 app，且首个迁移都是 0001_initial。"""
        assert legacy_chain_records()
        for app, names in LEGACY_4X_CHAIN.items():
            assert names[0] == "0001_initial", app

    def test_pre_split_current_tables_exist_in_models(self):
        """切分前判定依赖「当前表名」：它在模型里必须真实存在。

        写错当前表名会让「旧表在、新表不在」在等价库上恒成立，把可认领的库误判为
        切分前的旧库（升级被无辜拦下）——这里按模型定义逐一比对。
        """
        from django.apps import apps

        known = {model._meta.db_table for model in apps.get_models()}
        for legacy, current in PRE_SPLIT_TABLES:
            assert current in known, f"{current} 不是当前模型表名"
            assert legacy not in known, f"{legacy} 已是当前模型表名，不应作为切分前特征"


@pytest.mark.django_db
class TestAgainstRealDatabase:
    def test_current_database_is_consistent(self):
        ledger = collect_ledger(connection)
        assert ledger["migrations"]["orphan"] == []
        assert evaluate_ledger(ledger) == "consistent"

    def test_reconcile_is_noop_when_consistent(self):
        result = reconcile(connection)
        assert result == {"verdict": "consistent", "removed": [], "recorded": []}

    def test_legacy_orphan_removed_and_claimable_recorded(self):
        """4.x 游离记录被删除，合并链缺失记录被补记（不执行 DDL）。"""
        recorder = MigrationRecorder(connection)
        recorder.record_applied("identity", "0002_deptinfo_rules_userinfo_rules_userrole_menu")
        recorder.migration_qs.filter(app="identity", name="0002_initial").delete()

        ledger = collect_ledger(connection)
        assert "identity.0002_deptinfo_rules_userinfo_rules_userrole_menu" in ledger["migrations"]["legacy_orphan"]
        assert evaluate_ledger(ledger) == "reconcilable"

        result = reconcile(connection)
        assert result["removed"] == ["identity.0002_deptinfo_rules_userinfo_rules_userrole_menu"]
        assert result["recorded"] == ["identity.0002_initial"]

        recheck = collect_ledger(connection)
        assert recheck["migrations"]["orphan"] == []
        assert evaluate_ledger(recheck) == "consistent"

    def test_unknown_orphan_blocks_write(self):
        recorder = MigrationRecorder(connection)
        recorder.record_applied("legacy_app", "0001_initial")
        with pytest.raises(LedgerError):
            reconcile(connection)
        # 未知链路的记录必须原样保留（拒绝写入）
        assert recorder.migration_qs.filter(app="legacy_app", name="0001_initial").exists()

    def test_current_database_has_no_pre_split_signal(self):
        """等价链的库（四域切分后）：不得出现切分前特征，否则升级被误拦。"""
        assert collect_schema_ledger(connection)["pre_split_tables"] == []

    def test_pre_split_detected_from_legacy_table(self):
        """旧表在、当前表不在 → 判定为切分前的旧库。"""
        original = connection.introspection.table_names

        def fake_table_names(cursor):
            names = [name for name in original(cursor) if name != "identity_userinfo"]
            return [*names, "system_userinfo"]

        with mock.patch.object(connection.introspection, "table_names", side_effect=fake_table_names):
            schema = collect_schema_ledger(connection)
        assert {"legacy": "system_userinfo", "current": "identity_userinfo"} in schema["pre_split_tables"]

    def test_reconcile_blocks_pre_split_database(self):
        """切分前的旧库即便游离记录可识别，也必须拒绝认领（不写任何记录）。"""
        original = connection.introspection.table_names

        def fake_table_names(cursor):
            names = [name for name in original(cursor) if name != "identity_userinfo"]
            return [*names, "system_userinfo"]

        with mock.patch.object(connection.introspection, "table_names", side_effect=fake_table_names):
            with pytest.raises(LedgerError):
                reconcile(connection)


@pytest.mark.django_db
class TestCommandOutput:
    def test_json_output(self):
        out = StringIO()
        call_command("upgrade_ledger", "--json", stdout=out)
        payload = json.loads(out.getvalue())
        assert payload["verdict"] == "consistent"
        assert payload["applied_changes"] is False

    def test_apply_and_unknown_chain_exit_code(self):
        recorder = MigrationRecorder(connection)
        recorder.record_applied("legacy_app", "0001_initial")
        out, err = StringIO(), StringIO()
        with pytest.raises(SystemExit) as excinfo:
            call_command("upgrade_ledger", "--apply", "--json", stdout=out, stderr=err)
        assert excinfo.value.code == 1
        assert json.loads(out.getvalue())["verdict"] == "unsupported"

    def test_human_readable_output(self):
        out = StringIO()
        call_command("upgrade_ledger", stdout=out)
        rendered = out.getvalue()
        assert "upgrade-ledger" in rendered
        assert "结论：consistent" in rendered
