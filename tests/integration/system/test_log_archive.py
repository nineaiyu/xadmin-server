# -*- coding: utf-8 -*-
"""审计日志冷归档与冷热分层集成测试。

口径钉死：

- **删必已归档**：清理边界由归档水位驱动；未归档时清理不动数据，
  边界月（未整月超期）与未归档月一律保留（最多多留一个月，保留期是下限）；
- 归档产物 = ``JSONL.gz`` + ``.sha256`` sidecar + ``.manifest.json``（行数 / 时间范围 / 校验和），
  同月已归档时幂等跳过；
- 归档失败必须跳过清理（保数据优先），不得「未归档即删除」；
- ``--restore-range`` 语义 = 流式读取归档（不落库），支持子串过滤与条数上限。
"""

import datetime

import pytest
from django.core.management import call_command
from django.utils import timezone

from system.models import OperationLog, UserLoginLog
from system.utils import log_archive
from system.utils.ctasks import auto_clean_operation_log

pytestmark = pytest.mark.django_db

RETENTION_DAYS = 30


def _make_logs(count, when, status_code=1000):
    """批量造日志并把 created_time 覆写为指定历史时间（auto_now_add 需 update 绕过）。"""
    rows = OperationLog.objects.bulk_create(
        [
            OperationLog(module="archive-test", method="GET", path="/api/archive-test", status_code=status_code)
            for _ in range(count)
        ]
    )
    OperationLog.objects.filter(pk__in=[row.pk for row in rows]).update(created_time=when)
    return rows


def _clean_time():
    return timezone.now() - datetime.timedelta(days=RETENTION_DAYS)


class TestArchiveMonth:
    def test_roundtrip_manifest_checksum_and_restore(self, tmp_path):
        when = timezone.now() - datetime.timedelta(days=300)
        _make_logs(3, when)
        month = log_archive.month_of(when)

        manifest = log_archive.archive_month("operation", month, directory=tmp_path)

        assert manifest["rows"] == 3
        assert manifest["month"] == month
        assert manifest["first_time"] and manifest["last_time"]
        gz_path = tmp_path / f"operation-{month}.jsonl.gz"
        assert gz_path.exists() and manifest["bytes"] == gz_path.stat().st_size
        sidecar = (tmp_path / f"operation-{month}.sha256").read_text(encoding="utf-8")
        assert sidecar.startswith(manifest["sha256"])

        restored = list(log_archive.read_restore_rows("operation", month, directory=tmp_path, limit=0))
        assert len(restored) == 3
        assert {row["module"] for row in restored} == {"archive-test"}
        assert all(row["created_time"] for row in restored)

        verified = log_archive.verify_archive("operation", month, directory=tmp_path)
        assert verified["ok"] is True
        assert verified["rows"] == 3

    def test_idempotent_skip_keeps_existing_file(self, tmp_path):
        when = timezone.now() - datetime.timedelta(days=300)
        _make_logs(2, when)
        month = log_archive.month_of(when)

        first = log_archive.archive_month("operation", month, directory=tmp_path)
        assert first.get("skipped") is None
        gz_path = tmp_path / f"operation-{month}.jsonl.gz"
        content = gz_path.read_bytes()

        second = log_archive.archive_month("operation", month, directory=tmp_path)

        assert second["skipped"] is True
        assert gz_path.read_bytes() == content

    def test_verify_detects_tampering(self, tmp_path):
        when = timezone.now() - datetime.timedelta(days=300)
        _make_logs(1, when)
        month = log_archive.month_of(when)
        log_archive.archive_month("operation", month, directory=tmp_path)

        # 篡改内容（追加一行）后校验必须失败
        gz_path = tmp_path / f"operation-{month}.jsonl.gz"
        import gzip
        import json

        with gzip.open(gz_path, "at", encoding="utf-8") as gz:
            gz.write(json.dumps({"id": "tampered", "module": "hacked"}) + "\n")

        verified = log_archive.verify_archive("operation", month, directory=tmp_path)
        assert verified["ok"] is False
        assert verified["rows"] != verified["expected_rows"]

    def test_grep_and_limit(self, tmp_path):
        old = timezone.now() - datetime.timedelta(days=300)
        _make_logs(5, old)
        month = log_archive.month_of(old)
        log_archive.archive_month("operation", month, directory=tmp_path)

        limited = list(log_archive.read_restore_rows("operation", month, directory=tmp_path, limit=2))
        assert len(limited) == 2
        matched = list(
            log_archive.read_restore_rows("operation", month, directory=tmp_path, grep="不存在的关键字", limit=0)
        )
        assert matched == []

    def test_missing_archive_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            list(log_archive.read_restore_rows("operation", "2000-01", directory=tmp_path))


class TestWatermarkDrivenPrune:
    def test_not_archived_blocks_prune(self, tmp_path):
        old = timezone.now() - datetime.timedelta(days=200)
        _make_logs(3, old)

        # 未归档：水位停在最早数据所在月月初（该月之前无数据），清理删不到任何行
        watermark = log_archive.archive_watermark("operation", RETENTION_DAYS, tmp_path)
        expected_start, _ = log_archive.parse_month(log_archive.month_of(old))
        assert watermark == expected_start
        assert log_archive.prune_archived("operation", RETENTION_DAYS, None, tmp_path) == 0
        assert OperationLog.objects.count() == 3

    def test_archive_then_prune_flow(self, tmp_path):
        old = timezone.now() - datetime.timedelta(days=200)
        recent = timezone.now() - datetime.timedelta(days=1)
        _make_logs(3, old)
        _make_logs(2, recent)

        result = log_archive.archive_expired("operation", retention_days=RETENTION_DAYS, directory=tmp_path)
        assert len(result["archived"]) == 1
        archived_month = result["archived"][0]["month"]
        assert log_archive.verify_archive("operation", archived_month, directory=tmp_path)["ok"] is True

        # 水位停在「未整月超期」的边界月月初：边界由 clean_time 所在月决定
        watermark = log_archive.archive_watermark("operation", RETENTION_DAYS, tmp_path)
        expected_start, _ = log_archive.parse_month(log_archive.month_of(_clean_time()))
        assert watermark == expected_start

        deleted = log_archive.prune_archived("operation", RETENTION_DAYS, None, tmp_path)
        assert deleted == 3
        assert OperationLog.objects.count() == 2

    def test_boundary_month_rows_are_kept(self, tmp_path):
        # 40 天前：已超期（>30 天）但所在月尚未整月超期 → 不归档、不删除（最多多留一个月）
        boundary = timezone.now() - datetime.timedelta(days=RETENTION_DAYS + 10)
        _make_logs(2, boundary)
        month = log_archive.month_of(boundary)
        month_start, _ = log_archive.parse_month(month)
        month_end = log_archive.parse_month(log_archive.next_month(month))[0]
        if month_end <= _clean_time():
            pytest.skip("本月已整月超期（用例前提是边界月），跳过")

        result = log_archive.archive_expired("operation", retention_days=RETENTION_DAYS, directory=tmp_path)

        assert result["archived"] == []
        assert (tmp_path / f"operation-{month}.jsonl.gz").exists() is False
        watermark = log_archive.archive_watermark("operation", RETENTION_DAYS, tmp_path)
        assert watermark == month_start
        assert log_archive.prune_archived("operation", RETENTION_DAYS, None, tmp_path) == 0
        assert OperationLog.objects.count() == 2

    def test_error_log_layering_kept(self, tmp_path):
        # 错误日志（status != 1000）保留期更长：归档后仍在错误保留窗口内则不删
        old = timezone.now() - datetime.timedelta(days=200)
        _make_logs(2, old, status_code=1001)
        log_archive.archive_expired("operation", retention_days=RETENTION_DAYS, directory=tmp_path)

        # 错误保留期设为 365 天（> 全量 30 天）→ 200 天前的错误日志不该被删
        deleted = log_archive.prune_archived("operation", RETENTION_DAYS, 365, tmp_path)
        assert deleted == 0
        assert OperationLog.objects.count() == 2

        # 分层关闭（错误保留期 <= 全量）→ 跟随全量窗口删除
        deleted = log_archive.prune_archived("operation", RETENTION_DAYS, RETENTION_DAYS, tmp_path)
        assert deleted == 2
        assert OperationLog.objects.count() == 0

    def test_retention_disabled_keeps_everything(self, tmp_path):
        _make_logs(2, timezone.now() - datetime.timedelta(days=400))
        result = log_archive.archive_expired("operation", retention_days=0, directory=tmp_path)
        assert result["reason"]
        assert log_archive.prune_archived("operation", 0, None, tmp_path) == 0
        assert OperationLog.objects.count() == 2

    def test_archive_failure_skips_cleanup(self, tmp_path, monkeypatch):
        _make_logs(2, timezone.now() - datetime.timedelta(days=200))

        def _boom(*args, **kwargs):
            raise RuntimeError("archive storage down")

        monkeypatch.setattr("system.utils.log_archive.archive_expired", _boom)
        with pytest.raises(RuntimeError):
            auto_clean_operation_log(clean_day=RETENTION_DAYS)
        # 归档失败 → 不清理（保数据优先）
        assert OperationLog.objects.count() == 2

    def test_cleanup_task_archives_before_prune(self, tmp_path, monkeypatch):
        monkeypatch.setattr("server.settings.LOG_ARCHIVE_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr("django.conf.settings.LOG_ARCHIVE_DIR", str(tmp_path), raising=False)
        old = timezone.now() - datetime.timedelta(days=200)
        _make_logs(3, old)

        deleted = auto_clean_operation_log(clean_day=RETENTION_DAYS)

        assert deleted == 3
        month = log_archive.month_of(old)
        assert (tmp_path / f"operation-{month}.jsonl.gz").exists()
        assert OperationLog.objects.count() == 0


class TestCommand:
    def test_command_smoke(self, tmp_path, capsys):
        # 300 天前 = 所在月整月超期（默认保留期 180 天），可被归档
        old = timezone.now() - datetime.timedelta(days=300)
        _make_logs(2, old)
        month = log_archive.month_of(old)
        directory = str(tmp_path)

        call_command("log_archive", "--dry-run", "--dir", directory)
        assert "将归档" in capsys.readouterr().out

        call_command("log_archive", "--dir", directory)
        assert "已归档" in capsys.readouterr().out

        call_command("log_archive", "--list", "--dir", directory)
        assert f"operation  {month}" in capsys.readouterr().out

        call_command("log_archive", "--verify", "--dir", directory)
        assert "[OK]" in capsys.readouterr().out

        call_command("log_archive", "--restore-range", month, "--dir", directory, "--limit", "1")
        captured = capsys.readouterr()
        assert "共输出 1 行" in captured.err

        call_command("log_archive", "--restore-range", month, "--dir", directory, "--format", "table")
        assert "created_time" in capsys.readouterr().out

        call_command("log_archive", "--prune", "--dry-run", "--dir", directory)
        assert "将删除" in capsys.readouterr().out

        call_command("log_archive", "--prune", "--dir", directory)
        assert "删除 2 行" in capsys.readouterr().out
        assert OperationLog.objects.count() == 0


def _make_login_logs(count, when):
    """批量造登录日志并把 created_time 覆写为指定历史时间。"""
    rows = UserLoginLog.objects.bulk_create(
        [
            UserLoginLog(login_type=UserLoginLog.LoginTypeChoices.USERNAME, status=True, ipaddress="127.0.0.1")
            for _ in range(count)
        ]
    )
    UserLoginLog.objects.filter(pk__in=[row.pk for row in rows]).update(created_time=when)
    return rows


class TestLoginLogArchive:
    """登录日志自动归档 / 清理（收口）：保留期 LOGIN_LOG_RETENTION_DAYS。"""

    def test_auto_clean_covers_login_log(self, tmp_path, monkeypatch):
        from common.core.config import SysConfig

        monkeypatch.setattr("django.conf.settings.LOG_ARCHIVE_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr(type(SysConfig), "LOGIN_LOG_RETENTION_DAYS", property(lambda self: 365), raising=False)
        old = timezone.now() - datetime.timedelta(days=400)
        _make_login_logs(2, old)

        auto_clean_operation_log()

        month = log_archive.month_of(old)
        assert (tmp_path / f"login-{month}.jsonl.gz").exists()
        assert UserLoginLog.objects.count() == 0

    def test_login_retention_disabled_keeps_everything(self, tmp_path, monkeypatch):
        from common.core.config import SysConfig

        monkeypatch.setattr("django.conf.settings.LOG_ARCHIVE_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr(type(SysConfig), "LOGIN_LOG_RETENTION_DAYS", property(lambda self: 0), raising=False)
        old = timezone.now() - datetime.timedelta(days=400)
        _make_login_logs(2, old)

        auto_clean_operation_log()

        assert UserLoginLog.objects.count() == 2
        assert not list(tmp_path.glob("login-*.jsonl.gz"))

    def test_login_verify_restore_prune_drill(self, tmp_path, monkeypatch, capsys):
        """冷归档恢复演练（login 链路）：归档 → 校验 → 离线恢复查询 → 水位驱动清理。"""
        from common.core.config import SysConfig

        monkeypatch.setattr("django.conf.settings.LOG_ARCHIVE_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr(type(SysConfig), "LOGIN_LOG_RETENTION_DAYS", property(lambda self: 365), raising=False)
        old = timezone.now() - datetime.timedelta(days=400)
        month = log_archive.month_of(old)
        _make_login_logs(3, old)

        result = log_archive.archive_expired("login", directory=tmp_path)
        assert result["archived"] and not result.get("reason")

        verified = log_archive.verify_archive("login", month, directory=tmp_path)
        assert verified["ok"] is True and verified["rows"] == 3

        rows = list(log_archive.read_restore_rows("login", month, directory=tmp_path, limit=2))
        assert len(rows) == 2 and rows[0]["login_type"] is not None

        deleted = log_archive.prune_archived("login", directory=tmp_path)
        assert deleted == 3 and UserLoginLog.objects.count() == 0

        # 再清理幂等（水位已推进、数据已删）且校验仍通过（归档产物未受影响）
        assert log_archive.prune_archived("login", directory=tmp_path) == 0
        assert log_archive.verify_archive("login", month, directory=tmp_path)["ok"] is True

    def test_login_prune_command_supported(self, tmp_path, monkeypatch, capsys):
        """命令面 --model login --prune 不再被拒绝（保留期未启用时给可读提示）。"""
        from common.core.config import SysConfig

        monkeypatch.setattr("django.conf.settings.LOG_ARCHIVE_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr(type(SysConfig), "LOGIN_LOG_RETENTION_DAYS", property(lambda self: 365), raising=False)
        old = timezone.now() - datetime.timedelta(days=400)
        month = log_archive.month_of(old)
        _make_login_logs(2, old)

        call_command("log_archive", "--model", "login", "--dir", str(tmp_path))
        assert "已归档" in capsys.readouterr().out

        call_command("log_archive", "--model", "login", "--prune", "--dir", str(tmp_path))
        assert "删除 2 行" in capsys.readouterr().out
        assert UserLoginLog.objects.count() == 0
        assert (tmp_path / f"login-{month}.jsonl.gz").exists()


class TestColdArchiveRestoreDrill:
    """季度演练池场景「从冷归档恢复查询」的命令级全链路演练。"""

    def test_drill_archive_verify_restore_and_tamper_detection(self, tmp_path, capsys):
        old = timezone.now() - datetime.timedelta(days=400)
        month = log_archive.month_of(old)
        _make_logs(2, old, status_code=1000)
        _make_logs(1, old, status_code=1001)  # 错误日志同月一并归档
        directory = str(tmp_path)

        # 1) 归档 + 校验（sha256 与行数）
        call_command("log_archive", "--dir", directory)
        call_command("log_archive", "--verify", "--dir", directory)
        assert "[OK]" in capsys.readouterr().out

        # 2) 恢复查询：按 --grep 定位到已知记录（流式读取，不落库）
        call_command(
            "log_archive", "--restore-range", month, "--grep", "archive-test", "--limit", "3", "--dir", directory
        )
        assert "共输出 3 行" in capsys.readouterr().err

        # 3) 篡改检测：向归档追加一行后 --verify 必须失败并退出码 1
        import gzip
        import json

        gz_path = tmp_path / f"operation-{month}.jsonl.gz"
        with gzip.open(gz_path, "at", encoding="utf-8") as gz:
            gz.write(json.dumps({"id": "tampered", "module": "hacked"}) + "\n")
        with pytest.raises(SystemExit) as exc:
            call_command("log_archive", "--verify", "--dir", directory)
        assert exc.value.code == 1
