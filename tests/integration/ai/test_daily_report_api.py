# -*- coding: utf-8 -*-
"""AI 日报端点集成测试：开关语义（默认关）+ 指标实时聚合 + 摘要走缓存。"""

import pytest
from django.core.cache import cache

from ai.services import daily_report

pytestmark = pytest.mark.django_db

URL = "/api/ai/assistant/daily-report"


@pytest.fixture(autouse=True)
def _clear_report_cache():
    cache.delete(daily_report.REPORT_CACHE_KEY)
    yield
    cache.delete(daily_report.REPORT_CACHE_KEY)


@pytest.fixture()
def fake_events(monkeypatch: pytest.MonkeyPatch) -> None:
    from system.services.monitor import events

    monkeypatch.setattr(events, "alert_counts", lambda: {"firing": 1}, raising=True)
    monkeypatch.setattr(events, "collect_error_events", lambda **kw: {"total": 0}, raising=True)
    monkeypatch.setattr(events, "collect_task_events", lambda **kw: {"total": 0}, raising=True)


class TestDailyReportEndpoint:
    def test_default_off_returns_enabled_false_only(self, auth_client):
        body = auth_client.get(URL).json()["data"]
        assert body == {"enabled": False, "links": daily_report.REPORT_LINKS}

    def test_enabled_returns_live_metrics_and_cached_summary(self, auth_client, fake_events):
        cache.set(
            daily_report.REPORT_CACHE_KEY,
            {"summary": "有一笔待办积压。", "summary_error": "", "generated_at": "2026-10-25T07:31:00+08:00"},
            daily_report.REPORT_CACHE_TTL,
        )
        from django.test import override_settings

        with override_settings(AI_DAILY_REPORT_ENABLED=True):
            body = auth_client.get(URL).json()["data"]
        assert body["enabled"] is True
        assert body["summary"] == "有一笔待办积压。"
        assert body["metrics"]["monitor_firing"] == 1
        assert body["links"]["operation_log"] == "/system/logs/operation/index"

    def test_enabled_without_cache_reports_empty_summary(self, auth_client, fake_events):
        from django.test import override_settings

        with override_settings(AI_DAILY_REPORT_ENABLED=True):
            body = auth_client.get(URL).json()["data"]
        assert body["enabled"] is True
        assert body["summary"] == "" and body["generated_at"] == ""
        assert body["metrics"]["approval_pending"] == 0

    def test_normal_user_without_permission_rejected(self, normal_user, fake_events):
        from django.test import override_settings
        from rest_framework.test import APIClient

        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        with override_settings(AI_DAILY_REPORT_ENABLED=True):
            response = client.get(URL)
        assert response.status_code == 403
