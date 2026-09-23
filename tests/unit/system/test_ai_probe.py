# -*- coding: utf-8 -*-
"""AI 模型能力探测单测：四项能力探测函数 + 画像判据 + 用途分流。

覆盖：JSON / tool_calls / reasoning / vision 四项探测的成功与失败路径（失败只记录
ok=False 不抛错）、probe_profile 的子集与 vision 追加、capability_ok 的 fail-closed、
native_tools_enabled（准入：开关 + 能力双门）、profile_for 的用途级回落。
"""

import json

import pytest

from common.sdk.ai.chat import AiSdkError
from system.models.ai import AiProfile
from system.utils.ai_config import (
    PURPOSE_CHAT,
    PURPOSE_STRUCTURED,
    native_tools_enabled,
    profile_for,
    set_active_profile,
)
from system.utils.ai_probe import (
    CAPABILITY_JSON,
    CAPABILITY_REASONING,
    CAPABILITY_TOOL_CALLS,
    CAPABILITY_VISION,
    capability_ok,
    probe_json,
    probe_profile,
    probe_reasoning,
    probe_tool_calls,
    probe_vision,
)

pytestmark = pytest.mark.django_db


class FakeClient:
    """协议级假客户端：按需返回内容 / 工具调用 / 抛错，并记录 last_reasoning。"""

    def __init__(self, *, content="", reasoning=None, tool_calls=None, fail=False):
        self.model = "fake-model"
        self.content = content
        self.reasoning = reasoning
        self.tool_calls = tool_calls or []
        self.fail = fail
        self.last_reasoning = None
        self.last_usage = None

    def chat(self, messages, **overrides):
        if self.fail:
            raise AiSdkError("provider rejected")
        self.last_reasoning = self.reasoning
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
        return self.content

    def chat_tools(self, messages, tools, tool_choice="auto", **overrides):
        if self.fail:
            raise AiSdkError("provider rejected tools")
        self.last_tool_calls = self.tool_calls
        return {"content": self.content, "tool_calls": self.tool_calls, "usage": None, "reasoning": None}


def call(name, client):
    probes = {
        CAPABILITY_JSON: probe_json,
        CAPABILITY_TOOL_CALLS: probe_tool_calls,
        CAPABILITY_REASONING: probe_reasoning,
        CAPABILITY_VISION: probe_vision,
    }
    return probes[name](client)


class TestProbeFunctions:
    def test_json_ok(self):
        key, entry = probe_json(FakeClient(content='{"status": "ok", "score": 7}'))
        assert key == CAPABILITY_JSON and entry["ok"] is True
        assert entry["at"]

    def test_json_unparseable(self):
        key, entry = probe_json(FakeClient(content="I cannot do that"))
        assert entry["ok"] is False and "JSON" in entry["detail"]

    def test_json_wrong_shape(self):
        _, entry = probe_json(FakeClient(content='{"status": "no"}'))
        assert entry["ok"] is False

    def test_json_provider_error(self):
        _, entry = probe_json(FakeClient(fail=True))
        assert entry["ok"] is False and entry["detail"] == "provider rejected"

    def test_tool_calls_ok(self):
        calls = [{"id": "1", "name": "xadmin_probe_echo", "arguments": '{"value": "xadmin"}'}]
        key, entry = probe_tool_calls(FakeClient(tool_calls=calls))
        assert key == CAPABILITY_TOOL_CALLS and entry["ok"] is True
        assert entry["probe_tools"] == ["xadmin_probe_echo"]

    def test_tool_calls_missing(self):
        _, entry = probe_tool_calls(FakeClient(content="用文本回答"))
        assert entry["ok"] is False

    def test_reasoning_ok_and_absent(self):
        _, hit = probe_reasoning(FakeClient(reasoning="thinking..."))
        _, miss = probe_reasoning(FakeClient(content="391"))
        assert hit["ok"] is True and hit["reasoning_chars"] > 0
        assert miss["ok"] is False and "reasoning_content" in miss["detail"]

    def test_vision_ok_and_error(self):
        _, ok = probe_vision(FakeClient(content="red, blue"))
        _, bad = probe_vision(FakeClient(fail=True))
        assert ok["ok"] is True
        assert bad["ok"] is False

    def test_probe_profile_subset_and_vision(self):
        profile = AiProfile(name="p", base_url="https://x.example.com/v1", api_key="", model="m")
        profile.api_key_plain = "sk-x"
        profile.save()
        calls = []

        def _factory(credentials, http_client=None):
            calls.append(credentials)
            return FakeClient(content='{"status": "ok", "score": 1}', reasoning="r")

        import system.utils.ai_probe as probe_module

        original = probe_module.ChatCompletionsClient
        probe_module.ChatCompletionsClient = _factory
        try:
            subset = probe_profile(profile, capabilities=[CAPABILITY_JSON])
            assert set(subset) == {CAPABILITY_JSON, "model", "probed_at"}
            full = probe_profile(profile, vision=True)
            assert set(full) == {
                CAPABILITY_JSON,
                CAPABILITY_TOOL_CALLS,
                CAPABILITY_REASONING,
                CAPABILITY_VISION,
                "model",
                "probed_at",
            }
            assert calls[0]["api_key"] == "sk-x"
        finally:
            probe_module.ChatCompletionsClient = original


class TestCapabilityJudgement:
    def test_capability_ok_fail_closed(self):
        class Row:
            capabilities = {"tool_calls": {"ok": True}, "json": {"ok": False}, "reasoning": "bad-shape"}

        row = Row()
        assert capability_ok(row, "tool_calls") is True
        assert capability_ok(row, "json") is False
        assert capability_ok(row, "reasoning") is False
        assert capability_ok(row, "vision") is False

    def test_native_tools_requires_switch_and_capability(self, settings):
        settings.AI_NATIVE_TOOLS_ENABLED = False
        profile = AiProfile.objects.create(
            name="s", base_url="https://x.example.com/v1", api_key="", model="m", purpose=PURPOSE_STRUCTURED
        )
        profile.capabilities = {"tool_calls": {"ok": True}}
        profile.save()
        set_active_profile(profile, True)
        assert native_tools_enabled() is False  # 开关关闭 → 回落 prompt-JSON

        settings.AI_NATIVE_TOOLS_ENABLED = True
        assert native_tools_enabled() is True
        profile.capabilities = {"tool_calls": {"ok": False}}
        profile.save()
        assert native_tools_enabled() is False  # 能力未通过

    def test_native_tools_false_without_profile(self, settings):
        settings.AI_NATIVE_TOOLS_ENABLED = True
        AiProfile.objects.all().delete()
        assert native_tools_enabled() is False  # Setting 通路无画像 → fail-safe


class TestPurposeRouting:
    def test_structured_falls_back_to_chat_profile(self):
        AiProfile.objects.all().delete()
        chat = AiProfile.objects.create(
            name="chat", base_url="https://a.example.com/v1", api_key="", model="m1", purpose=PURPOSE_CHAT
        )
        set_active_profile(chat, True)
        assert profile_for(PURPOSE_STRUCTURED).pk == chat.pk
        assert profile_for(PURPOSE_CHAT).pk == chat.pk

    def test_purpose_level_activation_exclusive(self):
        AiProfile.objects.all().delete()
        chat = AiProfile.objects.create(name="chat", base_url="u", api_key="", model="m1", purpose=PURPOSE_CHAT)
        struct = AiProfile.objects.create(
            name="struct", base_url="u", api_key="", model="m2", purpose=PURPOSE_STRUCTURED
        )
        other = AiProfile.objects.create(name="chat2", base_url="u", api_key="", model="m3", purpose=PURPOSE_CHAT)
        set_active_profile(chat, True)
        set_active_profile(struct, True)
        assert profile_for(PURPOSE_CHAT).pk == chat.pk
        assert profile_for(PURPOSE_STRUCTURED).pk == struct.pk
        # 同用途再次激活 → 顶掉旧行（用途级互斥）
        set_active_profile(other, True)
        assert profile_for(PURPOSE_CHAT).pk == other.pk
        assert AiProfile.objects.filter(is_active=True).count() == 2
        assert json.dumps(profile_for(PURPOSE_STRUCTURED).capabilities) == "{}"
