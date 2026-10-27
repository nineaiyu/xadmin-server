# -*- coding: utf-8 -*-
"""验证码观测大盘聚合：窗口过滤 / 分组口径 / 缓存。"""

import pytest
from django.core.cache import cache

from captcha import telemetry
from captcha.models import CaptchaLog
from captcha.stats import STATS_CACHE_KEY_PREFIX, captcha_stats

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _reset_telemetry_counters():
    # telemetry 健康度是进程级计数器；同 worker 其他用例的历史事件会污染断言
    with telemetry._lock:
        telemetry._fallback.clear()
        telemetry._stats.update(emitted=0, dispatched=0, buffered=0, dropped=0)
    yield


@pytest.fixture(autouse=True)
def _clean_cache():
    try:
        cache.delete_pattern("captcha_*")
    except AttributeError:
        pass
    yield
    try:
        cache.delete_pattern("captcha_*")
    except AttributeError:
        pass


def _log(**fields):
    defaults = {"phase": "verify", "type": "slide", "passed": True}
    defaults.update(fields)
    return CaptchaLog.objects.create(**defaults)


class TestCaptchaStats:
    def test_empty_stats_are_zeroed(self):
        data = captcha_stats(24)
        assert data["window_hours"] == 24
        assert data["summary"] == {
            "challenge": 0,
            "verify": 0,
            "verify_passed": 0,
            "verify_failed": 0,
            "consume": 0,
            "pass_rate": 0.0,
            "deny": 0,
            "deny_rate": 0.0,
            "latency_p50": 0,
        }
        assert data["by_type"] == []
        assert data["by_asset"] == []
        assert data["by_decision"] == []
        assert data["risk_distribution"] == [
            {"bucket": "0-19", "total": 0},
            {"bucket": "20-39", "total": 0},
            {"bucket": "40-59", "total": 0},
            {"bucket": "60-79", "total": 0},
            {"bucket": "80-100", "total": 0},
        ]
        assert data["top_reasons"] == []
        assert data["top_ips"] == []
        assert data["top_fingerprints"] == []
        assert data["top_ua"] == []
        assert data["policy"]["thresholds"] == {"allow": 20, "light": 40, "hard": 70, "deny": 90}
        assert data["trend"] == []
        assert data["telemetry"]["emitted"] == 0

    def test_groups_by_phase_type_and_decision(self):
        _log(phase="challenge")
        _log(type="slide", passed=True, decision="allow")
        _log(type="slide", passed=False, decision="challenge")
        _log(type="click", passed=True, decision="allow")
        _log(phase="consume", passed=True)
        data = captcha_stats(24)
        summary = data["summary"]
        assert summary == {
            "challenge": 1,
            "verify": 3,
            "verify_passed": 2,
            "verify_failed": 1,
            "consume": 1,
            "pass_rate": 0.6667,
            "deny": 0,
            "deny_rate": 0.0,
            "latency_p50": 0,
        }
        by_type = {row["type"]: row for row in data["by_type"]}
        assert by_type["slide"] == {"type": "slide", "total": 2, "passed": 1, "pass_rate": 0.5}
        assert by_type["click"] == {"type": "click", "total": 1, "passed": 1, "pass_rate": 1.0}
        by_decision = {row["decision"]: row["total"] for row in data["by_decision"]}
        assert by_decision == {"allow": 2, "challenge": 1}
        assert len(data["trend"]) == 1
        assert data["trend"][0]["total"] == 3
        assert data["trend"][0]["passed"] == 2

    def test_groups_by_asset(self):
        _log(type="slide", passed=True, asset_id="scene-05")
        _log(type="slide", passed=False, asset_id="scene-05")
        _log(type="click", passed=True, asset_id="abstract-01")
        data = captcha_stats(24)
        by_asset = {row["asset_id"]: row for row in data["by_asset"]}
        assert by_asset["scene-05"] == {
            "asset_id": "scene-05",
            "total": 2,
            "passed": 1,
            "pass_rate": 0.5,
        }
        assert by_asset["abstract-01"]["pass_rate"] == 1.0

    def test_risk_distribution_and_top_deny_breakdowns(self):
        _log(
            decision="deny",
            passed=False,
            risk_score=95,
            risk_reasons=["honeypot"],
            ip_hash="ip-a",
            fp_hash="fp-a",
            ua_class="headless",
        )
        _log(
            decision="deny",
            passed=False,
            risk_score=82,
            risk_reasons=["honeypot", "freq_ip"],
            ip_hash="ip-a",
            fp_hash="fp-b",
            ua_class="headless",
        )
        _log(decision="allow", passed=True, risk_score=5, ip_hash="ip-b", fp_hash="fp-c", ua_class="desktop")
        data = captcha_stats(24)
        assert data["summary"]["deny"] == 2
        assert data["summary"]["deny_rate"] == 0.6667
        buckets = {row["bucket"]: row["total"] for row in data["risk_distribution"]}
        assert buckets == {"0-19": 1, "20-39": 0, "40-59": 0, "60-79": 0, "80-100": 2}
        assert data["top_reasons"][0] == {"reason": "honeypot", "total": 2}
        assert data["top_ips"] == [{"ip_hash": "ip-a", "total": 2}]
        assert {row["fp_hash"] for row in data["top_fingerprints"]} == {"fp-a", "fp-b"}
        assert data["top_ua"] == [{"ua_class": "headless", "total": 2}]

    def test_shadow_would_decision_feeds_dashboard(self):
        _log(decision="", would_decision="deny", passed=False, risk_score=95, risk_reasons=["honeypot"], ip_hash="ip-s")
        data = captcha_stats(24)
        assert data["summary"]["deny"] == 1
        assert data["summary"]["deny_rate"] == 1.0
        assert data["by_decision"] == [{"decision": "deny", "total": 1}]
        assert data["top_reasons"] == [{"reason": "honeypot", "total": 1}]
        assert data["top_ips"] == [{"ip_hash": "ip-s", "total": 1}]

    def test_latency_p50(self):
        _log(latency_ms=100)
        _log(latency_ms=300)
        _log(latency_ms=200)
        assert captcha_stats(24)["summary"]["latency_p50"] == 200

    def test_window_filters_old_rows(self):
        from datetime import timedelta

        from django.utils import timezone

        old = _log()
        CaptchaLog.objects.filter(pk=old.pk).update(ts=timezone.now() - timedelta(hours=48))
        _log()
        assert captcha_stats(24)["summary"]["verify"] == 1
        assert captcha_stats(72)["summary"]["verify"] == 2

    def test_window_is_clamped(self):
        assert captcha_stats(0)["window_hours"] == 1
        assert captcha_stats(9999)["window_hours"] == 168

    def test_result_is_cached_per_window(self):
        _log()
        first = captcha_stats(24)
        _log()
        # 60s 缓存内不重复扫表
        assert captcha_stats(24)["summary"]["verify"] == first["summary"]["verify"]
        cache.delete(f"{STATS_CACHE_KEY_PREFIX}_24")
        assert captcha_stats(24)["summary"]["verify"] == 2

    def test_cache_failure_still_returns_data(self, monkeypatch):
        _log()
        monkeypatch.setattr(cache, "get", lambda *_a, **_k: (_ for _ in ()).throw(ConnectionError("down")))
        monkeypatch.setattr(cache, "set", lambda *_a, **_k: (_ for _ in ()).throw(ConnectionError("down")))
        assert captcha_stats(24)["summary"]["verify"] == 1
