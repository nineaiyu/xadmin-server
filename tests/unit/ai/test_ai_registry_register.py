# -*- coding: utf-8 -*-
"""AI 动作注册与知识库批量删除的防御纵深守护。"""

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.test import APIRequestFactory, force_authenticate

from ai.services.actions.ai_registry import action_specs, register_action, reset_action_specs_cache
from integrations.sdk.ai.chat import AiSdkError

pytestmark = pytest.mark.django_db


class TestRegisterActionConflict:
    def teardown_method(self):
        reset_action_specs_cache()

    def test_register_conflicting_with_declaration_fails_fast(self):
        """与声明文件 key 冲突在 register 时即拒绝，不等首个工具目录构建才爆。"""
        from ai.services.actions.ai_actions import ACTION_SPECS

        existing_key = next(iter(ACTION_SPECS))
        with pytest.raises(ImproperlyConfigured, match="声明文件"):
            register_action(type("Spec", (), {"key": existing_key})())

    def test_register_unique_extra_key_succeeds(self):
        register_action(type("Spec", (), {"key": "plugin.custom.action"})())
        assert "plugin.custom.action" in action_specs()


class TestKnowledgeBatchDestroyInput:
    def test_malformed_elements_rejected_with_readable_code(self, superuser):
        """批量删除载荷含 dict 等畸形元素：可读 1004 拒绝，不裸抛 500。"""
        from ai.views.knowledge import AiKnowledgeDocumentViewSet

        view = AiKnowledgeDocumentViewSet.as_view({"post": "batch_destroy"})
        request = APIRequestFactory().post("/api/ai/knowledge-documents/batch-destroy", [{"pk": 1}], format="json")
        force_authenticate(request, user=superuser)
        response = view(request)
        assert response.data["code"] == 1004

    def test_scalar_elements_pass_validation(self, superuser):
        """合法标量元素进入既有删除流程（空集返回成功，不因校验误伤）。"""
        from ai.views.knowledge import AiKnowledgeDocumentViewSet

        view = AiKnowledgeDocumentViewSet.as_view({"post": "batch_destroy"})
        request = APIRequestFactory().post("/api/ai/knowledge-documents/batch-destroy", [], format="json")
        force_authenticate(request, user=superuser)
        response = view(request)
        assert response.data["code"] == 1000


class TestTrackedChatFailureInvalidatesCache:
    def test_failed_call_invalidates_usage_cache(self, monkeypatch, superuser):
        """失败调用同样失效用量汇总缓存（与成功路径同口径，防配额口径漂移）。"""
        from ai.services.assistant import ai_usage

        invalidated = []

        class _BrokenClient:
            model = "test-model"

            def chat(self, _messages, **_overrides):
                raise AiSdkError("provider down")

        monkeypatch.setattr(ai_usage, "invalidate_usage_cache", lambda user: invalidated.append(user))
        monkeypatch.setattr(ai_usage, "record_usage", lambda *a, **k: None)
        with pytest.raises(AiSdkError):
            ai_usage.tracked_chat(superuser, "test", [{"role": "user", "content": "hi"}], client=_BrokenClient())
        assert invalidated == [superuser]
