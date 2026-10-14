# -*- coding: utf-8 -*-
"""升级前体检命令（upgrade_check）守护：判定分支 + 真实库对账 + JSON 输出与退出码。"""

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder

from system.management.commands.upgrade_check import collect_report, evaluate


def _report(orphan=(), legacy_orphan=(), pending=(), missing_tables=(), missing_columns=()):
    return {
        "migrations": {
            "applied": 1,
            "pending": list(pending),
            "orphan": list(orphan),
            "legacy_orphan": list(legacy_orphan),
            "unknown_orphan": list(orphan),
        },
        "schema": {
            "tables_checked": 1,
            "missing_tables": list(missing_tables),
            "missing_columns": list(missing_columns),
        },
    }


class TestEvaluate:
    def test_up_to_date(self):
        assert evaluate(_report()) == ("up-to-date", 0)

    def test_pending_marks_needs_migrate(self):
        assert evaluate(_report(pending=["dataset.0002_webhookdelivery_generation"])) == ("needs-migrate", 0)

    def test_orphan_wins_over_pending(self):
        report = _report(orphan=["system.0005_ai_profile"], pending=["dataset.0002_x"])
        assert evaluate(report) == ("legacy-chain", 1)

    def test_legacy_orphan_is_reconcilable(self):
        report = _report(legacy_orphan=["system.0002_monitor_monitoralert"])
        assert evaluate(report) == ("legacy-chain-reconcilable", 0)

    def test_missing_table_marks_schema_drift(self):
        assert evaluate(_report(missing_tables=["dataset_dynamicform"])) == ("schema-drift", 1)

    def test_missing_column_marks_schema_drift(self):
        report = _report(missing_columns=[{"table": "identity_userinfo", "columns": ["date_expired"]}])
        assert evaluate(report) == ("schema-drift", 1)

    def test_schema_drift_beats_reconcilable(self):
        report = _report(legacy_orphan=["system.0002_monitor_monitoralert"], missing_tables=["system_monitor"])
        assert evaluate(report) == ("schema-drift", 1)


@pytest.mark.django_db
class TestAgainstRealDatabase:
    def test_current_database_is_consistent(self):
        """测试库刚跑完全链迁移：无游离记录、无缺表缺列。"""
        report = collect_report(connection)
        assert report["migrations"]["orphan"] == []
        assert report["schema"]["missing_tables"] == []
        assert report["schema"]["missing_columns"] == []
        assert evaluate(report) == ("up-to-date", 0)

    def test_orphan_record_detected(self):
        recorder = MigrationRecorder(connection)
        recorder.record_applied("legacy_app", "0001_initial")
        report = collect_report(connection)
        assert "legacy_app.0001_initial" in report["migrations"]["orphan"]
        assert evaluate(report) == ("legacy-chain", 1)

    def test_legacy_4x_record_is_reconcilable(self):
        """4.2.5 链的游离记录可认领（不阻断升级），与未知链路区分开。"""
        recorder = MigrationRecorder(connection)
        recorder.record_applied("task", "0003_periodictaskowner")
        report = collect_report(connection)
        assert "task.0003_periodictaskowner" in report["migrations"]["legacy_orphan"]
        assert report["migrations"]["unknown_orphan"] == []
        assert evaluate(report) == ("legacy-chain-reconcilable", 0)


@pytest.mark.django_db
class TestCommandOutput:
    def test_json_output(self):
        out = StringIO()
        call_command("upgrade_check", "--json", stdout=out)
        payload = json.loads(out.getvalue())
        assert payload["verdict"] == "up-to-date"
        assert payload["exit_code"] == 0
        assert payload["schema"]["tables_checked"] > 0

    def test_legacy_chain_exits_nonzero(self):
        MigrationRecorder(connection).record_applied("legacy_app", "0001_initial")
        out = StringIO()
        with pytest.raises(SystemExit) as excinfo:
            call_command("upgrade_check", "--json", stdout=out)
        assert excinfo.value.code == 1
        assert json.loads(out.getvalue())["verdict"] == "legacy-chain"

    def test_human_readable_output(self):
        out = StringIO()
        call_command("upgrade_check", stdout=out)
        rendered = out.getvalue()
        assert "upgrade-check" in rendered
        assert "结论：up-to-date" in rendered
