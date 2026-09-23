# -*- coding: utf-8 -*-
"""AI 能力线 API 集成测试：能力探测 / 原生工具双轨 / 幂等 / 用量。

桩 LLM 按请求体分流：带 tools 的请求返回 tool_calls（原生轨道），否则按内容返回
JSON 或 ping 文本（存量 prompt-JSON 轨道）——同一桩同时覆盖双轨对照。
"""

import json
import json as jsonlib

import pytest

from system.models.ai import AiProfile, AiUsageRecord
from system.models.log import OperationLog
from system.utils.ai_actions import ACTION_DASHBOARD_OVERVIEW
from system.utils.ai_config import PURPOSE_STRUCTURED, set_active_profile

pytestmark = pytest.mark.django_db

PROFILES_URL = "/api/system/ai/profiles"
ASSISTANT_URL = "/api/system/ai/assistant"


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeStreamResponse:
    """流式响应桩：SSE 帧序列（走真实 chat_stream 解析）。"""

    status_code = 200
    text = "provider rejected"

    def __init__(self, lines):
        self._lines = lines

    def iter_lines(self, decode_unicode=True):
        yield from self._lines


def _sse_lines(draft_payload):
    frame = json.dumps({"choices": [{"delta": {"content": draft_payload}}]}, ensure_ascii=False)
    return [f"data: {frame}".encode(), b"data: [DONE]"]


class StubLLM:
    """双轨桩：tools 请求 → tool_calls；流式 → SSE 草稿帧；其余按 prompt 内容分流。"""

    def __init__(self):
        self.requests = []

    def post(self, url, json=None, headers=None, timeout=None, stream=False, **kwargs):
        body = json or {}
        self.requests.append(body)
        if stream:
            draft = jsonlib.dumps(
                {"actions": [{"action": ACTION_DASHBOARD_OVERVIEW, "params": {}, "summary": "看总览"}]},
                ensure_ascii=False,
            )
            return _FakeStreamResponse(_sse_lines(draft))
        if body.get("tools"):
            payload = {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": ACTION_DASHBOARD_OVERVIEW,
                                        "arguments": jsonlib.dumps({"_summary": "看总览"}),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            }
            return _FakeResponse(payload)
        prompt = str(body.get("messages") or "")
        if "capability probe" in prompt:
            content = '{"status": "ok", "score": 7}'
        elif "17 times 23" in prompt:
            content = "391"
        else:
            content = "provider ok"
        return _FakeResponse(
            {
                "choices": [{"message": {"content": content, "reasoning_content": "thinking"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }
        )


def _iter_stream(response):
    """流式响应字节块（测试用同步收集；SSE 已切异步迭代器）。"""
    content = response.streaming_content
    if getattr(response, "is_async", False):
        from asgiref.sync import async_to_sync

        async def gather():
            return [chunk async for chunk in content]

        return async_to_sync(gather)()
    return list(content)


@pytest.fixture(autouse=True)
def _clean_profiles():
    AiProfile.objects.all().delete()
    AiUsageRecord.objects.all().delete()
    yield
    AiProfile.objects.all().delete()
    AiUsageRecord.objects.all().delete()


@pytest.fixture
def ai_settings(settings):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_BASE_URL = "https://ai.example.com/v1"
    settings.AI_API_KEY = "sk-test"
    settings.AI_MODEL = "test-model"
    settings.AI_ACTION_ENABLED = True
    settings.AI_NATIVE_TOOLS_ENABLED = False
    return settings


@pytest.fixture
def stub_llm(monkeypatch):
    stub = StubLLM()
    monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient._client", lambda self: stub)
    return stub


def make_profile(*, purpose="chat", capabilities=None):
    profile = AiProfile.objects.create(
        name=f"p-{purpose}", base_url="https://ai.example.com/v1", api_key="", model="test-model", purpose=purpose
    )
    profile.api_key_plain = "sk-test"
    if capabilities is not None:
        profile.capabilities = capabilities
    profile.save()
    set_active_profile(profile, True)
    return profile


class TestProbeEndpoint:
    def test_probe_persists_capabilities(self, auth_client, ai_settings, stub_llm):
        profile = make_profile()
        response = auth_client.post(f"{PROFILES_URL}/{profile.pk}/probe", {"vision": False}, format="json")
        assert response.status_code == 200, response.data
        body = response.json()
        assert body["data"]["json"]["ok"] is True
        assert body["data"]["reasoning"]["ok"] is True  # 桩返回 reasoning_content
        assert body["data"]["tool_calls"]["ok"] is True  # 桩对 tools 请求返回 tool_calls
        profile.refresh_from_db()
        assert profile.capabilities["json"]["ok"] is True and profile.probed_at is not None

    def test_probe_requires_configuration(self, auth_client, ai_settings):
        profile = AiProfile.objects.create(name="empty", base_url="https://x.example.com/v1", api_key="", model="m")
        assert auth_client.post(f"{PROFILES_URL}/{profile.pk}/probe").json()["code"] == 1001

    def test_probe_unknown_capability(self, auth_client, ai_settings, stub_llm):
        profile = make_profile()
        response = auth_client.post(f"{PROFILES_URL}/{profile.pk}/probe", {"capabilities": ["nope"]}, format="json")
        assert response.json()["code"] == 1001

    def test_capabilities_manual_override(self, auth_client, ai_settings):
        profile = make_profile()
        payload = {"capabilities": {"tool_calls": {"ok": True, "detail": "手动修正"}}}
        response = auth_client.patch(f"{PROFILES_URL}/{profile.pk}", payload, format="json")
        assert response.status_code == 200, response.data
        profile.refresh_from_db()
        assert profile.capabilities["tool_calls"]["ok"] is True

    def test_capabilities_shape_validated(self, auth_client, ai_settings):
        profile = make_profile()
        response = auth_client.patch(
            f"{PROFILES_URL}/{profile.pk}", {"capabilities": {"tool_calls": "yes"}}, format="json"
        )
        assert response.status_code == 400


class TestNativeToolTrack:
    def test_native_track_used_when_enabled(self, auth_client, ai_settings, stub_llm):
        ai_settings.AI_NATIVE_TOOLS_ENABLED = True
        make_profile(purpose=PURPOSE_STRUCTURED, capabilities={"tool_calls": {"ok": True}})
        response = auth_client.post(
            f"{ASSISTANT_URL}/action/interpret/stream", {"message": "看看系统总览"}, format="json"
        )
        assert response.status_code == 200
        assert "text/event-stream" in response["Content-Type"]
        frames = b"".join(_iter_stream(response)).decode("utf-8")
        assert '"kind": "draft"' in frames or '"kind":"draft"' in frames
        assert ACTION_DASHBOARD_OVERVIEW in frames
        assert any(request.get("tools") for request in stub_llm.requests)  # 走原生 tools 通道
        # 原生轨道不再下发动作目录 prompt（同一份 schema 由 tools 定义承载）
        assert all(
            "ALLOWED_ACTIONS_JSON" not in jsonlib.dumps(request.get("messages")) for request in stub_llm.requests
        )

    def test_prompt_track_when_capability_missing(self, auth_client, ai_settings, stub_llm):
        ai_settings.AI_NATIVE_TOOLS_ENABLED = True
        make_profile(purpose=PURPOSE_STRUCTURED, capabilities={"tool_calls": {"ok": False}})
        response = auth_client.post(
            f"{ASSISTANT_URL}/action/interpret/stream", {"message": "看看系统总览"}, format="json"
        )
        assert response.status_code == 200
        frames = b"".join(_iter_stream(response)).decode("utf-8")
        assert ACTION_DASHBOARD_OVERVIEW in frames  # 存量 prompt-JSON 轨道仍可用
        # 回落轨道：不带 tools、且 prompt 里保留了动作目录标记（存量桩/E2E 依赖）
        assert all(not request.get("tools") for request in stub_llm.requests)
        assert any(
            "ALLOWED_ACTIONS_JSON" in jsonlib.dumps(request.get("messages"), ensure_ascii=False)
            for request in stub_llm.requests
        )


class TestIdempotency:
    def test_duplicate_execute_reported(self, auth_client, ai_settings, stub_llm):
        make_profile()
        payload = {"action": ACTION_DASHBOARD_OVERVIEW, "params": {}}
        first = auth_client.post(f"{ASSISTANT_URL}/action/execute", payload, format="json").json()
        second = auth_client.post(f"{ASSISTANT_URL}/action/execute", payload, format="json").json()
        assert first["code"] == 1000 and second["code"] == 1000
        assert first["data"]["deduplicated"] is False
        assert second["data"]["deduplicated"] is True
        assert second["data"]["draft_id"] == first["data"]["draft_id"]
        audits = OperationLog.objects.filter(module="AI:action")
        assert audits.count() == 2
        assert any("deduplicated" in (row.changes or "") for row in audits)

    def test_force_channel_reexecutes(self, auth_client, ai_settings, stub_llm):
        make_profile()
        payload = {"action": ACTION_DASHBOARD_OVERVIEW, "params": {}}
        auth_client.post(f"{ASSISTANT_URL}/action/execute", payload, format="json")
        forced = auth_client.post(f"{ASSISTANT_URL}/action/execute", {**payload, "force": True}, format="json").json()
        assert forced["data"]["deduplicated"] is False


class TestUsageEndpoint:
    def test_usage_summary(self, auth_client, superuser, ai_settings, stub_llm):
        from system.utils.ai_usage import invalidate_usage_cache, record_usage

        record_usage(superuser, "docs", usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3})
        invalidate_usage_cache(superuser)
        response = auth_client.get(f"{ASSISTANT_URL}/usage?days=7")
        assert response.status_code == 200, response.data
        data = response.json()["data"]
        assert data["total_calls"] >= 1
        assert data["total_tokens"] >= 3
        assert data["quota"]["daily_calls"] == 0
        assert any(row["feature"] == "docs" for row in data["by_feature"])

    def test_quota_blocks_calls(self, auth_client, superuser, ai_settings, stub_llm, monkeypatch):
        from common.core.config import SysConfig

        monkeypatch.setattr(type(SysConfig), "AI_QUOTA_USER_DAILY_CALLS", property(lambda self: 1), raising=False)
        make_profile()
        from system.utils.ai_usage import invalidate_usage_cache, record_usage

        record_usage(superuser, "docs")
        invalidate_usage_cache(superuser)
        response = auth_client.post(f"{ASSISTANT_URL}/ask", {"question": "什么是 xadmin"}, format="json")
        assert response.json()["code"] == 1001
