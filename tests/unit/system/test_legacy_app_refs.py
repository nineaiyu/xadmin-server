# -*- coding: utf-8 -*-
"""二开 app 迁移引用改写的守护。

覆盖四类风险：
1. 外键目标改写错（``to="system.UserInfo"`` 必须落到新域）；
2. 依赖节点改写错（指向已消失节点的元组要换成当前初始迁移）；
3. 映射不到时**瞎猜**（必须留作未解决项交人工，不静默改坏）；
4. 改写不幂等 / dry-run 直接落盘。
"""

from system.utils.platform import legacy_app_refs

MIGRATION = """
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("system", "0002_operationlog_exec_time_operationlog_request_uuid"),
    ]

    operations = [
        migrations.CreateModel(
            name="Ticket",
            fields=[
                ("id", models.BigAutoField(primary_key=True)),
                ("owner", models.ForeignKey(to="system.UserInfo", on_delete=django.db.models.deletion.CASCADE)),
                ("dept", models.ForeignKey(to="system.DeptInfo", on_delete=django.db.models.deletion.CASCADE)),
            ],
        ),
    ]
"""

MOVES = {"system.userinfo": "identity.userinfo", "system.deptinfo": "identity.deptinfo"}


def _app(tmp_path, body: str = MIGRATION):
    app = tmp_path / "my_app"
    (app / "migrations").mkdir(parents=True)
    (app / "migrations" / "__init__.py").write_text("", encoding="utf-8")
    (app / "migrations" / "0001_initial.py").write_text(body, encoding="utf-8")
    return app


class TestRewrite:
    def test_dry_run_reports_diff_without_writing(self, tmp_path):
        app = _app(tmp_path)
        before = (app / "migrations" / "0001_initial.py").read_text(encoding="utf-8")
        result = legacy_app_refs.rewrite_app(app, MOVES, dry_run=True)
        assert result["dry_run"] is True
        assert result["files"] and result["diff"]
        assert (app / "migrations" / "0001_initial.py").read_text(encoding="utf-8") == before

    def test_apply_rewrites_fk_targets_dependencies_and_adds_app_dependency(self, tmp_path):
        app = _app(tmp_path)
        legacy_app_refs.rewrite_app(app, MOVES, dry_run=False)
        text = (app / "migrations" / "0001_initial.py").read_text(encoding="utf-8")
        assert 'to="identity.UserInfo"' in text
        assert 'to="identity.DeptInfo"' in text
        assert "system.UserInfo" not in text
        # 指向已消失节点的依赖换成当前初始迁移，并补上目标 app 的依赖
        assert '("system", "0001_initial")' in text
        assert "0002_operationlog" not in text
        assert '("identity", "0001_initial")' in text

    def test_rewrite_is_idempotent(self, tmp_path):
        app = _app(tmp_path)
        legacy_app_refs.rewrite_app(app, MOVES, dry_run=False)
        first = (app / "migrations" / "0001_initial.py").read_text(encoding="utf-8")
        again = legacy_app_refs.rewrite_app(app, MOVES, dry_run=False)
        assert again["files"] == [] and again["diff"] == []
        assert (app / "migrations" / "0001_initial.py").read_text(encoding="utf-8") == first

    def test_unmapped_reference_is_reported_not_guessed(self, tmp_path):
        app = _app(tmp_path, MIGRATION.replace("system.UserInfo", "system.CustomTable"))
        result = legacy_app_refs.rewrite_app(app, MOVES, dry_run=False)
        text = (app / "migrations" / "0001_initial.py").read_text(encoding="utf-8")
        assert 'to="system.CustomTable"' in text  # 原样保留，不猜
        assert result["unresolved"] and "system.customtable" in result["unresolved"][0]

    def test_scan_is_read_only(self, tmp_path):
        app = _app(tmp_path)
        before = (app / "migrations" / "0001_initial.py").read_text(encoding="utf-8")
        findings = legacy_app_refs.scan_app(app, MOVES)
        assert findings["fk_targets"]
        assert findings["dependencies"]
        assert (app / "migrations" / "0001_initial.py").read_text(encoding="utf-8") == before


class TestLabelMapFromTag:
    def test_repo_tag_derives_domain_moves(self):
        moves = legacy_app_refs.label_moves_from_tag("v4.2.5")
        if not moves:  # pragma: no cover - 单仓检出无 tag
            import pytest

            pytest.skip("仓库内没有 v4.2.5 的历史（单仓检出）")
        for old, new in (
            ("system.userinfo", "identity.userinfo"),
            ("system.deptinfo", "identity.deptinfo"),
            ("system.userrole", "identity.userrole"),
            ("system.uploadfile", "file.uploadfile"),
            ("common.monitor", "system.monitor"),
        ):
            assert moves[old] == new

    def test_current_initial_lookup(self):
        assert legacy_app_refs.current_initial("identity") == "0001_initial"
        assert legacy_app_refs.current_initial("no_such_app") is None
