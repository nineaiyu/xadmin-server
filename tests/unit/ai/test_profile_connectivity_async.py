# -*- coding: utf-8 -*-
"""AI「测试连接」下沉 celery（PERF-AI-01）。

守护点：

1. ``POST .../test`` 只排队并返回 task_id（请求线程不再外呼供应商）；
2. ``GET .../test-result`` 按 task_id 回读结论，形状固定（task_id / state / ok / detail）；
3. 未配置档案、缺 task_id 都走既有 1001 口径；
4. 任务本身不外抛：供应商失败归一成 ``{"ok": False, "detail": ...}``。
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from ai.models import AiProfile

pytestmark = pytest.mark.django_db


@pytest.fixture
def profile(db):
    item = AiProfile(name="单测档案", base_url="https://api.example.com/v1", model="gpt-test", is_active=False)
    item.api_key_plain = "sk-test"  # 走密文 setter：api_key 为加密串，直接写明文会被判「未配置」
    item.save()
    return item


@pytest.fixture
def client(superuser):
    api = APIClient()
    api.force_authenticate(superuser)
    return api


class TestSubmitTest:
    def test_test_only_enqueues_task(self, client, profile, monkeypatch):
        """提交端点只排队：返回 task_id 即可，不自己外呼供应商。"""
        queued = {"pk": None}

        class _Queued:
            id = "task-123"
            state = "PENDING"
            result = None

        def _delay(profile_pk):
            queued["pk"] = profile_pk
            return _Queued()

        monkeypatch.setattr("ai.tasks.test_ai_profile_task.delay", _delay)

        resp = client.post(f"/api/ai/profiles/{profile.pk}/test", {}, format="json")

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 1000
        assert body["data"] == {"task_id": "task-123", "state": "PENDING"}
        assert queued["pk"] == str(profile.pk)

    def test_unconfigured_profile_rejected(self, client, db):
        draft = AiProfile.objects.create(name="草稿", base_url="", api_key="", model="")
        resp = client.post(f"/api/ai/profiles/{draft.pk}/test", {}, format="json")
        assert resp.json()["code"] == 1001


class TestResultEndpoint:
    def test_missing_task_id(self, client, profile):
        resp = client.get(f"/api/ai/profiles/{profile.pk}/test-result")
        assert resp.json()["code"] == 1001

    def test_eager_mode_returns_result_inline(self, client, profile, monkeypatch):
        """eager（测试 / E2E）没有结果后端：结论随提交响应直接带回，前端无需轮询。"""
        monkeypatch.setattr(
            "integrations.sdk.ai.chat.ChatCompletionsClient.chat",
            lambda self, messages: "pong",
        )
        data = client.post(f"/api/ai/profiles/{profile.pk}/test", {}, format="json").json()["data"]

        assert data["task_id"]
        assert data["state"] == "SUCCESS"
        assert data["ok"] is True
        assert data["detail"] == "pong"

    def test_unknown_task_reports_pending(self, client, profile):
        """结果后端查不到的任务（未开结果后端 / 已过期）按未完成返回，形状不变。"""
        resp = client.get(f"/api/ai/profiles/{profile.pk}/test-result", {"task_id": "no-such-task"})
        data = resp.json()["data"]
        assert data["task_id"] == "no-such-task"
        assert data["state"] in {"PENDING", "FAILURE"}


class TestTaskNormalizesFailure:
    def test_provider_error_becomes_readable_result(self, profile, monkeypatch):
        from ai.tasks import test_ai_profile_task

        class _Failing:
            def __init__(self, *args, **kwargs):
                pass

            def chat(self, messages):
                raise RuntimeError("dial tcp: connection refused")

        monkeypatch.setattr("integrations.sdk.ai.chat.ChatCompletionsClient", _Failing)

        result = test_ai_profile_task.apply(args=[str(profile.pk)]).get()

        assert result["ok"] is False
        assert "connection refused" in result["detail"]
