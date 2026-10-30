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

    def test_unknown_task_rejected(self, client, profile):
        """未登记归属的 task_id 一律拒绝：不再把全局任务命名空间当存在性探针。"""
        resp = client.get(f"/api/ai/profiles/{profile.pk}/test-result", {"task_id": "no-such-task"})
        assert resp.json()["code"] == 1001

    def test_task_result_requires_owner(self, client, profile, monkeypatch):
        """task_id → (档案, 提交者) 归属绑定：他人拿到 task_id 也读不到结论。"""
        from identity.models import UserInfo

        class _Queued:
            id = "task-owner"
            state = "PENDING"
            result = None

        monkeypatch.setattr("ai.tasks.test_ai_profile_task.delay", lambda profile_pk: _Queued())
        submitted = client.post(f"/api/ai/profiles/{profile.pk}/test", {}, format="json").json()["data"]
        assert submitted["task_id"] == "task-owner"

        # 提交者本人可回读
        mine = client.get(f"/api/ai/profiles/{profile.pk}/test-result", {"task_id": "task-owner"})
        assert mine.json()["code"] == 1000

        # 另一个超管即使能看到同一档案，也不能读走他人任务结论
        other = UserInfo.objects.create_superuser(
            username="admin-other", email="other@example.com", password="Admin@123456"
        )
        other_client = APIClient()
        other_client.force_authenticate(other)
        resp = other_client.get(f"/api/ai/profiles/{profile.pk}/test-result", {"task_id": "task-owner"})
        assert resp.json()["code"] == 1001


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
