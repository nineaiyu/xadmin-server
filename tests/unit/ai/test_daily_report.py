# -*- coding: utf-8 -*-
"""AI 主动日报单测：指标聚合 / 摘要生成与降级 / 缓存读写 / 开关空转。

摘要链路不真连 LLM：tracked_chat 与结构化客户端分别打桩；指标聚合打桩
system 侧事件收集器（本测试不关心监控口径本身，只验拼装与兜底）。
"""

from typing import Any

import pytest

from ai.models.ai import AiUsageRecord
from ai.services import daily_report

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_usage():
    from django.core.cache import cache

    def _clear():
        cache.delete(daily_report.METRICS_CACHE_KEY)

    AiUsageRecord.objects.all().delete()
    _clear()
    yield
    AiUsageRecord.objects.all().delete()
    _clear()


@pytest.fixture()
def fake_events(monkeypatch: pytest.MonkeyPatch) -> None:
    from system.services.monitor import events

    monkeypatch.setattr(events, "alert_counts", lambda: {"firing": 2, "total_24h": 3, "resolved_24h": 1}, raising=True)
    monkeypatch.setattr(
        events,
        "collect_error_events",
        lambda **kwargs: {"total": 5, "results": [], "truncated": False},
        raising=True,
    )
    monkeypatch.setattr(
        events,
        "collect_task_events",
        lambda **kwargs: {"total": 1, "results": [], "truncated": False},
        raising=True,
    )


class TestCollectMetrics:
    def test_montage_from_event_collectors(self, fake_events: None) -> None:
        from approval.models.approval_request import ApprovalRequest

        ApprovalRequest.objects.all().delete()
        AiUsageRecord.objects.create(feature=AiUsageRecord.Feature.DAILY, tokens_total=128)
        metrics = daily_report.collect_metrics()
        assert metrics == {
            "approval_pending": 0,
            "monitor_firing": 2,
            "error_requests_24h": 5,
            "task_failed_24h": 1,
            "ai_calls_24h": 1,
            "ai_tokens_24h": 128,
        }

    def test_single_metric_failure_falls_back_to_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from system.services.monitor import events

        def boom(**kwargs: Any) -> Any:
            raise RuntimeError("module cropped")

        monkeypatch.setattr(events, "alert_counts", boom, raising=True)
        monkeypatch.setattr(events, "collect_error_events", lambda **kw: {"total": 0}, raising=True)
        monkeypatch.setattr(events, "collect_task_events", lambda **kw: {"total": 0}, raising=True)
        metrics = daily_report.collect_metrics()
        assert metrics["monitor_firing"] == 0


class TestGenerateDailyReport:
    def test_switch_off_noop(self) -> None:
        assert daily_report.generate_daily_report() == {}

    def test_generate_stores_summary(self, fake_events: None, monkeypatch: pytest.MonkeyPatch) -> None:
        from django.core.cache import cache

        monkeypatch.setattr(daily_report, "daily_report_enabled", lambda: True)
        recorded: dict[str, Any] = {}

        def fake_summarize(metrics: dict[str, Any]) -> str:
            recorded["metrics"] = metrics
            return "全站运行平稳。"

        monkeypatch.setattr(daily_report, "summarize_metrics", fake_summarize, raising=True)
        payload = daily_report.generate_daily_report()
        assert payload["summary"] == "全站运行平稳。"
        assert payload["summary_error"] == ""
        assert payload["generated_at"]
        assert recorded["metrics"]["monitor_firing"] == 2
        cached = cache.get(daily_report.REPORT_CACHE_KEY)
        assert isinstance(cached, dict) and cached["summary"] == "全站运行平稳。"

    def test_summary_failure_degrades_but_metrics_kept(
        self, fake_events: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(daily_report, "daily_report_enabled", lambda: True)

        def boom(metrics: dict[str, Any]) -> str:
            raise RuntimeError("LLM unreachable")

        monkeypatch.setattr(daily_report, "summarize_metrics", boom, raising=True)
        payload = daily_report.generate_daily_report()
        assert payload["summary"] == ""
        assert "LLM unreachable" in payload["summary_error"]
        assert payload["metrics"]["error_requests_24h"] == 5


class TestSummarizeMetrics:
    def test_prompt_covers_all_metrics_and_records_usage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ai.services.assistant import ai_usage

        calls: dict[str, Any] = {}

        class _Client:
            max_tokens = 512

        def fake_tracked_chat(user: Any, feature: str, messages: list[Any], client: Any = None, **kw: Any) -> str:
            calls.update(user=user, feature=feature, messages=messages, max_tokens=kw.get("max_tokens"))
            return "ok"

        monkeypatch.setattr(ai_usage, "tracked_chat", fake_tracked_chat, raising=True)
        from ai.services.assistant import ai_config

        monkeypatch.setattr(ai_config, "structured_chat_client", lambda: (_Client(), 512), raising=True)
        text = daily_report.summarize_metrics({"approval_pending": 3, "monitor_firing": 0})
        assert text == "ok"
        assert calls["feature"] == AiUsageRecord.Feature.DAILY
        assert calls["user"] is None
        assert calls["max_tokens"] == 512
        prompt = "".join(part["content"] for part in calls["messages"])
        assert "approval_pending: 3" in prompt and "monitor_firing: 0" in prompt


class TestDailyReportPayload:
    def test_metrics_served_from_short_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """首页高频读取：TTL 内复用缓存，不再触发 5 组聚合。"""
        from django.core.cache import cache
        from django.test import override_settings

        cache.set(daily_report.METRICS_CACHE_KEY, {"monitor_firing": 9}, daily_report.METRICS_CACHE_TTL)

        def boom() -> Any:
            raise AssertionError("collect_metrics should not run while cache is warm")

        monkeypatch.setattr(daily_report, "collect_metrics", boom)
        with override_settings(AI_DAILY_REPORT_ENABLED=True):
            data = daily_report.daily_report_payload()
        assert data["metrics"] == {"monitor_firing": 9}

    def test_disabled_returns_minimal(self) -> None:
        from django.test import override_settings

        with override_settings(AI_DAILY_REPORT_ENABLED=False):
            data = daily_report.daily_report_payload()
        assert data["enabled"] is False
        assert "metrics" not in data
        assert data["links"]["approvals"] == "/approval/index"
        assert data["links"]["tasks"] == "/system/celery/logs/index"

    def test_enabled_merges_cached_summary_and_live_metrics(
        self, fake_events: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from django.core.cache import cache
        from django.test import override_settings

        cache.set(
            daily_report.REPORT_CACHE_KEY,
            {"summary": "旧摘要", "summary_error": "", "generated_at": "2026-10-25T07:31:00+08:00"},
            daily_report.REPORT_CACHE_TTL,
        )
        with override_settings(AI_DAILY_REPORT_ENABLED=True):
            data = daily_report.daily_report_payload()
        assert data["enabled"] is True
        assert data["summary"] == "旧摘要"
        assert data["generated_at"].startswith("2026-10-25")
        assert data["metrics"]["monitor_firing"] == 2
        cache.delete(daily_report.REPORT_CACHE_KEY)
