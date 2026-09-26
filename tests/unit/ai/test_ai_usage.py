# -*- coding: utf-8 -*-
"""AI 用量账本与配额单测：token 归一 + 记账 + 配额判定 + 并发信号量。

配额判定读账本当日汇总（60s 短缓存），这里用独立用户避免缓存串味；
并发信号量走缓存计数器，异常路径保证释放（with 上下文）。
"""

import pytest

from ai.models.ai import AiUsageRecord
from ai.utils.ai_usage import (
    acquire_stream_slot,
    extract_tokens,
    invalidate_usage_cache,
    quota_error,
    quota_limits,
    record_usage,
    release_stream_slot,
    today_usage,
    usage_summary,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_usage():
    AiUsageRecord.objects.all().delete()
    yield
    AiUsageRecord.objects.all().delete()


class TestTokenExtraction:
    def test_openai_shape(self):
        assert extract_tokens({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}) == {
            "tokens_in": 10,
            "tokens_out": 5,
            "tokens_total": 15,
        }

    def test_alias_shape_and_missing_total(self):
        assert extract_tokens({"input_tokens": 3, "output_tokens": 4})["tokens_total"] == 7

    def test_empty_usage(self):
        assert extract_tokens(None) == {"tokens_in": 0, "tokens_out": 0, "tokens_total": 0}


class TestUsageLedger:
    def test_record_and_today_usage(self, superuser):
        record_usage(superuser, "docs", usage={"total_tokens": 30}, duration_ms=12)
        record_usage(superuser, "chat", usage={"total_tokens": 20}, ok=False, detail="boom")
        invalidate_usage_cache(superuser)
        summary = today_usage(superuser)
        assert summary == {"calls": 2, "tokens": 50}
        row = AiUsageRecord.objects.filter(ok=False).first()
        assert row.detail == "boom" and row.feature == "chat"

    def test_summary_groups(self, superuser):
        record_usage(superuser, "docs", usage={"prompt_tokens": 1, "completion_tokens": 1})
        record_usage(superuser, "nl", usage={"total_tokens": 5}, ok=False)
        data = usage_summary(days=7)
        assert data["total_calls"] == 2 and data["total_tokens"] == 7 and data["failed"] == 1
        assert {row["feature"] for row in data["by_feature"]} == {"docs", "nl"}
        assert data["top_users"][0]["username"] == superuser.username
        assert data["quota"] == quota_limits()

    def test_summary_by_track_double_track(self, superuser):
        """双轨对照：按轨道聚合成功率；无轨道记录（非草稿链路）不入表。"""
        record_usage(superuser, "action", track="native", usage={"total_tokens": 10})
        record_usage(superuser, "action", track="prompt", usage={"total_tokens": 20})
        record_usage(superuser, "action", track="prompt", ok=False, detail="boom")
        record_usage(superuser, "docs", usage={"total_tokens": 5})  # 无轨道
        tracks = {row["track"]: row for row in usage_summary(days=7)["by_track"]}
        assert set(tracks) == {"native", "prompt"}
        assert tracks["native"]["calls"] == 1 and tracks["native"]["success_rate"] == 100.0
        assert tracks["prompt"]["calls"] == 2 and tracks["prompt"]["failed"] == 1
        assert tracks["prompt"]["success_rate"] == 50.0


class TestQuota:
    def test_unlimited_by_default(self, superuser, settings):
        settings.AI_QUOTA_USER_DAILY_CALLS = 0
        settings.AI_QUOTA_USER_DAILY_TOKENS = 0
        for _ in range(5):
            record_usage(superuser, "docs")
        invalidate_usage_cache(superuser)
        assert quota_error(superuser, "docs") == ""

    def test_call_quota_blocks(self, superuser, settings, monkeypatch):
        from common.core.config import SysConfig

        monkeypatch.setattr(type(SysConfig), "AI_QUOTA_USER_DAILY_CALLS", property(lambda self: 2), raising=False)
        record_usage(superuser, "docs")
        record_usage(superuser, "docs")
        invalidate_usage_cache(superuser)
        detail = quota_error(superuser, "docs")
        assert detail and "2/2" in detail

    def test_token_quota_blocks(self, superuser, monkeypatch):
        from common.core.config import SysConfig

        monkeypatch.setattr(type(SysConfig), "AI_QUOTA_USER_DAILY_TOKENS", property(lambda self: 10), raising=False)
        record_usage(superuser, "nl", usage={"total_tokens": 12})
        invalidate_usage_cache(superuser)
        detail = quota_error(superuser, "nl")
        assert detail and "12/10" in detail

    def test_stream_slot_roundtrip(self, monkeypatch):
        from common.core.config import SysConfig

        monkeypatch.setattr(type(SysConfig), "AI_QUOTA_MAX_CONCURRENT_STREAMS", property(lambda self: 1), raising=False)
        assert acquire_stream_slot() is True
        assert acquire_stream_slot() is False  # 达到上限
        release_stream_slot()
        assert acquire_stream_slot() is True
        release_stream_slot()

    def test_stream_slot_unlimited(self, monkeypatch):
        from common.core.config import SysConfig

        monkeypatch.setattr(type(SysConfig), "AI_QUOTA_MAX_CONCURRENT_STREAMS", property(lambda self: 0), raising=False)
        assert acquire_stream_slot() is True
        assert acquire_stream_slot() is True


class TestTrackedWrappers:
    def test_tracked_chat_records_usage(self, superuser, monkeypatch):
        from ai.utils.ai_usage import tracked_chat

        class Client:
            model = "m"
            last_usage = {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}

            def chat(self, messages, **overrides):
                return "ok"

        assert tracked_chat(superuser, "docs", [{"role": "user", "content": "hi"}], client=Client()) == "ok"
        row = AiUsageRecord.objects.get()
        assert (row.tokens_total, row.ok, row.model) == (5, True, "m")

    def test_tracked_chat_records_track(self, superuser):
        """双轨对照：包装器的 track 参数落账（prompt / native）。"""
        from ai.utils.ai_usage import tracked_chat

        class Client:
            model = "m"
            last_usage = None

            def chat(self, messages, **overrides):
                return "ok"

        tracked_chat(superuser, "action", [], client=Client(), track="prompt")
        assert AiUsageRecord.objects.get().track == "prompt"

    def test_tracked_chat_records_failure(self, superuser):
        from ai.utils.ai_usage import tracked_chat
        from common.sdk.ai.chat import AiSdkError

        class Client:
            model = "m"
            last_usage = None

            def chat(self, messages, **overrides):
                raise AiSdkError("boom")

        with pytest.raises(AiSdkError):
            tracked_chat(superuser, "docs", [], client=Client())
        row = AiUsageRecord.objects.get()
        assert row.ok is False and row.detail == "boom"

    def test_tracked_stream_releases_slot(self, superuser, monkeypatch):
        from ai.utils.ai_usage import stream_slots_in_use, tracked_chat_stream
        from common.core.config import SysConfig

        monkeypatch.setattr(type(SysConfig), "AI_QUOTA_MAX_CONCURRENT_STREAMS", property(lambda self: 1), raising=False)

        class Client:
            model = "m"
            last_usage = {"total_tokens": 7}

            def chat_stream(self, messages, **overrides):
                yield {"type": "content", "text": "a"}
                yield {"type": "content", "text": "b"}

        events = list(tracked_chat_stream(superuser, "docs", Client(), []))
        assert [item["text"] for item in events] == ["a", "b"]
        assert stream_slots_in_use() == 0  # 生成器结束后释放
        assert AiUsageRecord.objects.get().tokens_total == 7
