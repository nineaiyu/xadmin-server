# -*- coding: utf-8 -*-
"""观测层遥测：采样 / 降级缓冲 / 批量落库 / 清理命令 / 主链路隔离。"""

import time
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from captcha import telemetry
from captcha.models import CaptchaLog
from captcha.tasks import flush_captcha_logs

pytestmark = pytest.mark.django_db


def _reset_state() -> None:
    with telemetry._lock:
        telemetry._fallback.clear()
        telemetry._stats.update(emitted=0, dispatched=0, buffered=0, dropped=0)


@pytest.fixture(autouse=True)
def _reset_telemetry_state():
    # 进程级计数器会被同 worker 的其他用例污染，用例前必须归零
    _reset_state()
    yield
    _reset_state()


def _emit_ok(**extra):
    telemetry.emit(phase="verify", scene="login", passed=True, **extra)


class TestEmit:
    def test_disabled_emits_nothing(self, settings):
        settings.CAPTCHA_LOG_ENABLED = False
        _emit_ok()
        assert telemetry.stats()["emitted"] == 0
        assert CaptchaLog.objects.count() == 0

    def test_success_sample_rate_zero_drops_success_only(self, settings):
        settings.CAPTCHA_LOG_SUCCESS_SAMPLE_RATE = 0.0
        _emit_ok()
        telemetry.emit(phase="verify", scene="login", passed=False)
        stats = telemetry.stats()
        assert stats["emitted"] == 1  # 失败恒 100% 记录
        assert CaptchaLog.objects.filter(passed=False).count() == 1

    def test_emit_writes_row_via_eager_task(self):
        _emit_ok(action="identity.login.basic", type="slide")
        assert CaptchaLog.objects.filter(
            phase="verify", scene="login", action="identity.login.basic", passed=True
        ).exists()

    def test_emit_swallows_internal_errors(self, monkeypatch):
        monkeypatch.setattr(telemetry, "_emit_inner", lambda **_kw: (_ for _ in ()).throw(RuntimeError("boom")))
        telemetry.emit(phase="challenge")  # 不得抛出

    def test_only_known_fields_persisted(self):
        telemetry.emit(phase="verify", scene="login", passed=True, bogus_field="value")
        row = CaptchaLog.objects.get()
        assert not hasattr(row, "bogus_field")
        assert row.scene == "login"

    def test_fallback_buffer_overflow_drops_oldest(self, monkeypatch):
        monkeypatch.setattr(telemetry, "_dispatch", lambda _events: False)
        monkeypatch.setattr(telemetry, "FALLBACK_BUFFER_LIMIT", 2)
        for index in range(4):
            telemetry.emit(phase="challenge", scene=f"s{index}", passed=False)
        stats = telemetry.stats()
        assert stats["dropped"] == 2
        assert stats["fallback_pending"] == 2

    def test_flush_fallback_redispatches_after_recovery(self, monkeypatch):
        monkeypatch.setattr(telemetry, "_dispatch", lambda _events: False)
        _emit_ok()
        assert telemetry.stats()["fallback_pending"] == 1
        monkeypatch.undo()
        flushed = telemetry.flush_fallback()
        assert flushed == 1
        assert CaptchaLog.objects.count() == 1


class TestFlushTask:
    def test_bulk_write_skips_non_dict_events(self):
        written = flush_captcha_logs([{"phase": "challenge", "ts": time.time()}, "garbage", None])
        assert written == 1
        assert CaptchaLog.objects.count() == 1

    def test_task_failure_swallowed(self, monkeypatch):
        monkeypatch.setattr(CaptchaLog.objects, "bulk_create", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError))
        assert flush_captcha_logs([{"phase": "challenge"}]) == 0


class TestPurge:
    def _log(self, days_ago: float) -> CaptchaLog:
        return CaptchaLog.objects.create(
            phase="verify",
            ts=timezone.now() - timedelta(days=days_ago),
        )

    def test_purge_respects_retention(self, settings):
        settings.CAPTCHA_LOG_RETENTION_DAYS = 7
        old = self._log(10)
        fresh = self._log(1)
        deleted = CaptchaLog.purge_expired_logs()
        assert deleted == 1
        assert not CaptchaLog.objects.filter(id=old.id).exists()
        assert CaptchaLog.objects.filter(id=fresh.id).exists()

    def test_zero_retention_disables_purge(self, settings):
        settings.CAPTCHA_LOG_RETENTION_DAYS = 0
        self._log(365)
        assert CaptchaLog.purge_expired_logs() == 0
        assert CaptchaLog.objects.count() == 1

    def test_management_command(self):
        import io

        self._log(60)
        self._log(0)
        out = io.StringIO()
        call_command("captcha_purge_logs", "--days", "30", stdout=out)
        assert "purged=1" in out.getvalue()
        assert CaptchaLog.objects.count() == 1


class TestHelpers:
    def test_hash_ip_stable_and_salted(self, settings):
        settings.SECRET_KEY = "seed-a"
        first = telemetry.hash_ip("10.0.0.1")
        assert first == telemetry.hash_ip("10.0.0.1")
        assert first != telemetry.hash_ip("10.0.0.2")
        assert telemetry.hash_ip("") == ""
        settings.SECRET_KEY = "seed-b"
        assert telemetry.hash_ip("10.0.0.1") != first

    def test_classify_ua(self):
        assert telemetry.classify_ua("Mozilla/5.0 HeadlessChrome/120") == "headless"
        assert telemetry.classify_ua("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)") == "mobile"
        assert telemetry.classify_ua("Mozilla/5.0 (Windows NT 10.0) Chrome/126.0") == "desktop"
        assert telemetry.classify_ua("curl/8.4.0") == "other"
        assert telemetry.classify_ua("") == ""

    def test_browser_family(self):
        assert telemetry.browser_family("Mozilla/5.0 Chrome/126.0.0.0 Safari/537.36").startswith("Chrome/")
        assert telemetry.browser_family("") == ""

    def test_build_log_row_converts_ts(self):
        row = telemetry.build_log_row({"phase": "challenge", "ts": 1730000000.5})
        assert row.ts is not None
        assert row.ts.year == 2024
