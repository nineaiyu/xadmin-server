# -*- coding: utf-8 -*-
"""迁移账本认领守护：判定分支 + 真实库对账 + 认领写入（幂等）。"""

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder

from system.utils.platform.migration_ledger import (
    LEGACY_4X_CHAIN,
    LedgerError,
    collect_ledger,
    evaluate_ledger,
    legacy_chain_records,
    reconcile,
)


def _ledger(legacy_orphan=(), unknown_orphan=(), missing_tables=(), missing_columns=(), claimable=()):
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

    def test_legacy_whitelist_covers_every_business_app(self):
        """白名单必须覆盖全部迁移合并过的业务 app，且首个迁移都是 0001_initial。"""
        assert legacy_chain_records()
        for app, names in LEGACY_4X_CHAIN.items():
            assert names[0] == "0001_initial", app


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
