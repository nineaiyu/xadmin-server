# -*- coding: utf-8 -*-
"""升级前体检命令（upgrade_check）守护：判定分支 + 真实库对账 + JSON 输出与退出码。"""

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder

from system.management.commands.upgrade_check import collect_report, evaluate


def _report(orphan=(), legacy_orphan=(), pending=(), missing_tables=(), missing_columns=(), applied=1, pre_split=()):
    return {
        "migrations": {
            "applied": applied,
            "pending": list(pending),
            "orphan": list(orphan),
            "legacy_orphan": list(legacy_orphan),
            "unknown_orphan": list(orphan),
        },
        "schema": {
            "tables_checked": 1,
            "missing_tables": list(missing_tables),
            "missing_columns": list(missing_columns),
            "pre_split_tables": list(pre_split),
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

    def test_empty_database_is_needs_migrate_not_drift(self):
        """全新空库（首次安装）：没有任何已应用迁移时，缺表是 migrate 的正常前态。

        空库的 django_migrations 为空、业务表一张没有，若按「缺表 = schema-drift」判定，
        首次安装会被体检拦下（安装器 preflight 默认中止）。
        """
        report = _report(
            applied=0,
            pending=["system.0001_initial", "dataset.0001_initial"],
            missing_tables=["system_menu", "identity_userinfo"],
        )
        assert evaluate(report) == ("needs-migrate", 0)

    def test_empty_database_with_unknown_orphan_still_blocks(self):
        """未知游离记录（旧链路的库）即便没有已应用迁移也不能放行。"""
        report = _report(applied=0, orphan=["legacy_app.0001_initial"])
        assert evaluate(report) == ("legacy-chain", 1)

    def test_missing_column_on_empty_database_is_needs_migrate(self):
        report = _report(
            applied=0,
            pending=["identity.0001_initial"],
            missing_columns=[{"table": "identity_userinfo", "columns": ["date_expired"]}],
        )
        assert evaluate(report) == ("needs-migrate", 0)

    def test_pre_split_database_is_blocked_with_dedicated_verdict(self):
        """四域切分前的旧库（表名仍是 system_*）单独判定，不做账本认领。

        这类库的表名与列集都与合并链不一致——认领只写迁移记录，无法把它变成 5.0 的库，
        必须走「清库重建 + 回灌」；判定也不能笼统归到 legacy-chain（那样会误导用户以为
        版本号够就能原地升级）。
        """
        report = _report(pre_split=[{"legacy": "system_userinfo", "current": "identity_userinfo"}])
        assert evaluate(report) == ("legacy-chain-pre-split", 1)

    def test_pre_split_wins_over_reconcilable(self):
        """即使游离记录全部命中白名单，切分前的旧库也不得进入认领路径。"""
        report = _report(
            legacy_orphan=["system.0002_monitor_monitoralert"],
            pre_split=[{"legacy": "system_userinfo", "current": "identity_userinfo"}],
        )
        assert evaluate(report) == ("legacy-chain-pre-split", 1)


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
