# -*- coding: utf-8 -*-
"""声明式动作 dispatch 异常：内部原文只进日志，不回传对话流。"""

from types import SimpleNamespace

import pytest

from ai.services.actions import ai_api_actions
from ai.services.actions.ai_api_actions import api_action, execute_api_action

pytestmark = pytest.mark.django_db


def test_dispatch_exception_hidden(superuser, monkeypatch):
    spec = api_action("boom", "Boom", "", "GET", "/api/system/monitor/overview", {})

    def boom(request, **kwargs):
        raise RuntimeError("internal-marker /tmp/secret.sql")

    monkeypatch.setattr(ai_api_actions, "resolve", lambda path: SimpleNamespace(func=boom, kwargs={}))
    result = execute_api_action(spec, superuser, {})

    assert result["ok"] is False
    assert "internal-marker" not in result["detail"]
    assert "/tmp/secret.sql" not in result["detail"]
