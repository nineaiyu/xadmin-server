# -*- coding: utf-8 -*-
"""AI 配置档案（AiProfile）集成测试。

覆盖：CRUD 与 api_key 加密/回显剔除、激活互斥（全局至多一个）、
credentials 收口（激活档案优先 / 无档案回落 Setting 通路）、
test 连接测试动作、persona/context_limit 配置化、权限门控。
"""

import pytest
from rest_framework.test import APIClient

from system.models.ai import AiProfile
from system.utils.ai import (
    ai_context_limit,
    ai_credentials,
    ai_persona,
    is_configured,
    set_active_profile,
)

pytestmark = pytest.mark.django_db

PROFILES_URL = "/api/system/ai/profiles"


@pytest.fixture(autouse=True)
def _clean_profiles():
    AiProfile.objects.all().delete()
    yield
    AiProfile.objects.all().delete()


@pytest.fixture
def profile_payload():
    return {
        "name": "主档案",
        "base_url": "https://ai.example.com/v1",
        "api_key": "sk-super-secret",
        "model": "deepseek-chat",
        "temperature": 0.5,
        "max_tokens": 2048,
        "top_p": 0.9,
        "frequency_penalty": 0.3,
        "presence_penalty": 0.1,
        "stop": "观察,结束",
        "seed": 7,
        "timeout": 45,
        "max_retries": 2,
        "context_limit": 30,
        "persona": "You are a helpful ops assistant.",
        "is_active": True,
        "remark": "主力配置",
    }


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def stub_llm(monkeypatch):
    class _Stub:
        calls = []

        @staticmethod
        def post(url, **kwargs):
            _Stub.calls.append({"url": url, **kwargs})
            return _FakeResponse({"choices": [{"message": {"content": "provider ok"}}]})

    _Stub.calls = []
    monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient._client", lambda self: _Stub())
    return _Stub.calls


class TestProfileCrud:
    def test_anonymous_rejected(self, api_client):
        assert api_client.get(PROFILES_URL).status_code == 401

    def test_create_encrypts_key_and_never_echoes(self, auth_client, profile_payload):
        response = auth_client.post(PROFILES_URL, profile_payload, format="json")
        assert response.status_code == 200, response.data
        body = response.json()["data"]
        assert "api_key" not in body
        assert body["api_key_set"] is True
        row = AiProfile.objects.get(name="主档案")
        assert "sk-super-secret" not in (row.api_key or "")
        assert row.api_key_plain == "sk-super-secret"

    def test_update_blank_key_keeps_old(self, auth_client, profile_payload):
        auth_client.post(PROFILES_URL, profile_payload, format="json")
        row = AiProfile.objects.get(name="主档案")
        response = auth_client.patch(f"{PROFILES_URL}/{row.pk}", {"temperature": 0.9}, format="json")
        assert response.status_code == 200, response.data
        row.refresh_from_db()
        assert row.api_key_plain == "sk-super-secret"
        assert row.temperature == 0.9

    def test_update_with_new_key_reencrypts(self, auth_client, profile_payload):
        auth_client.post(PROFILES_URL, profile_payload, format="json")
        row = AiProfile.objects.get(name="主档案")
        response = auth_client.patch(f"{PROFILES_URL}/{row.pk}", {"api_key": "sk-new-key"}, format="json")
        assert response.status_code == 200, response.data
        row.refresh_from_db()
        assert row.api_key_plain == "sk-new-key"
        assert "sk-new-key" not in row.api_key

    def test_validation_rejects_bad_input(self, auth_client, profile_payload):
        for field, patch, needle in (
            ("name", {"name": "  "}, "name"),
            ("base_url", {"base_url": "ftp://x"}, "http"),
        ):
            payload = {**profile_payload, "name": f"bad-{field}", **patch}
            response = auth_client.post(PROFILES_URL, payload, format="json")
            assert response.status_code == 400, response.data
            assert needle in str(response.data).lower()

    def test_normal_user_without_menu_rejected(self, normal_user):
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        assert client.get(PROFILES_URL).status_code == 403


class TestActivation:
    def test_create_active_switches_exclusively(self, auth_client, profile_payload):
        auth_client.post(PROFILES_URL, profile_payload, format="json")
        second = {**profile_payload, "name": "备用档案", "is_active": True}
        auth_client.post(PROFILES_URL, second, format="json")
        assert AiProfile.objects.filter(is_active=True).count() == 1
        assert AiProfile.objects.get(name="备用档案").is_active is True
        assert AiProfile.objects.get(name="主档案").is_active is False

    def test_activate_action_switches(self, auth_client, profile_payload):
        auth_client.post(PROFILES_URL, profile_payload, format="json")
        second = {**profile_payload, "name": "备用档案"}
        auth_client.post(PROFILES_URL, second, format="json")
        row = AiProfile.objects.get(name="备用档案")
        response = auth_client.post(f"{PROFILES_URL}/{row.pk}/activate")
        assert response.status_code == 200, response.data
        row.refresh_from_db()
        assert row.is_active is True
        assert AiProfile.objects.get(name="主档案").is_active is False

    def test_deactivate_falls_back_to_settings(self, auth_client, profile_payload, settings):
        settings.AI_BASE_URL = "https://fallback.example.com/v1"
        settings.AI_API_KEY = "sk-fallback"
        settings.AI_MODEL = "fallback-model"
        auth_client.post(PROFILES_URL, profile_payload, format="json")
        row = AiProfile.objects.get(name="主档案")
        assert is_configured() is True
        assert ai_credentials()["base_url"] == "https://ai.example.com/v1"
        response = auth_client.post(f"{PROFILES_URL}/{row.pk}/deactivate")
        assert response.status_code == 200, response.data
        row.refresh_from_db()
        assert row.is_active is False
        assert ai_credentials()["base_url"] == "https://fallback.example.com/v1"
        assert ai_credentials()["model"] == "fallback-model"

    def test_destroy_active_profile_falls_back(self, auth_client, profile_payload, settings):
        settings.AI_BASE_URL = "https://fallback.example.com/v1"
        settings.AI_API_KEY = "sk-fallback"
        settings.AI_MODEL = "fallback-model"
        auth_client.post(PROFILES_URL, profile_payload, format="json")
        row = AiProfile.objects.get(name="主档案")
        assert auth_client.delete(f"{PROFILES_URL}/{row.pk}").status_code in (200, 204)
        assert AiProfile.objects.count() == 0
        assert ai_credentials()["base_url"] == "https://fallback.example.com/v1"


class TestCredentialsResolution:
    def test_profile_takes_priority(self, settings):
        settings.AI_BASE_URL = "https://fallback.example.com/v1"
        profile = AiProfile.objects.create(name="p", base_url="https://profile.example.com/v1", api_key="", model="m")
        set_active_profile(profile, True)
        credentials = ai_credentials()
        assert credentials["base_url"] == "https://profile.example.com/v1"
        assert credentials["stop"] == []
        assert is_configured() is False  # api_key 为空 → 档案未配置

    def test_profile_full_params(self, profile_payload):
        profile = AiProfile(**{**profile_payload, "api_key": ""})
        profile.api_key_plain = "sk-x"
        profile.save()
        set_active_profile(profile, True)
        credentials = ai_credentials()
        assert credentials["api_key"] == "sk-x"
        assert credentials["temperature"] == 0.5
        assert credentials["max_tokens"] == 2048
        assert credentials["top_p"] == 0.9
        assert credentials["frequency_penalty"] == 0.3
        assert credentials["presence_penalty"] == 0.1
        assert credentials["stop"] == ["观察", "结束"]
        assert credentials["seed"] == 7
        assert credentials["max_retries"] == 2
        assert credentials["persona"] == "You are a helpful ops assistant."
        assert credentials["context_limit"] == 30

    def test_setting_fallback_without_profile(self, settings):
        settings.AI_TEMPERATURE = 0.3
        settings.AI_MAX_TOKENS = 0
        settings.AI_TOP_P = None
        settings.AI_STOP = "STOP"
        settings.AI_MAX_RETRIES = 1
        settings.AI_CONTEXT_LIMIT = 10
        credentials = ai_credentials()
        assert credentials["temperature"] == 0.3
        assert credentials["max_tokens"] is None
        assert credentials["top_p"] is None
        assert credentials["stop"] == "STOP"
        assert credentials["max_retries"] == 1
        assert ai_context_limit() == 10

    def test_persona_priority(self, settings):
        assert ai_persona()  # 内置兜底非空
        settings.AI_PERSONA = "setting-persona"
        assert ai_persona() == "setting-persona"
        profile = AiProfile.objects.create(name="p", base_url="u", api_key="", model="m", persona="profile-persona")
        set_active_profile(profile, True)
        assert ai_persona() == "profile-persona"
        profile.persona = "  "
        profile.save()
        assert ai_persona() == "setting-persona"  # 档案留空回落 Setting

    def test_context_limit_priority(self, settings):
        profile = AiProfile.objects.create(name="p", base_url="u", api_key="", model="m", context_limit=33)
        set_active_profile(profile, True)
        assert ai_context_limit() == 33
        profile.context_limit = 0
        profile.save()
        settings.AI_CONTEXT_LIMIT = 12
        assert ai_context_limit() == 12


class TestTestAction:
    def test_ping_by_profile(self, auth_client, profile_payload, stub_llm):
        auth_client.post(PROFILES_URL, profile_payload, format="json")
        row = AiProfile.objects.get(name="主档案")
        response = auth_client.post(f"{PROFILES_URL}/{row.pk}/test")
        assert response.status_code == 200, response.data
        assert "provider ok" in response.json()["detail"]
        request_body = stub_llm[0]["json"]
        assert request_body["temperature"] == 0.5
        assert request_body["stop"] == ["观察", "结束"]
        assert request_body["max_tokens"] == 2048

    def test_ping_incomplete_profile(self, auth_client):
        row = AiProfile.objects.create(name="p", base_url="https://x.example.com", api_key="", model="m")
        response = auth_client.post(f"{PROFILES_URL}/{row.pk}/test")
        assert response.json()["code"] == 1001
