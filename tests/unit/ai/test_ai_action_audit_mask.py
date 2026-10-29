# -*- coding: utf-8 -*-
"""AI 动作审计不得把 password/token 等参数原文落库。"""

import json

import pytest

from ai.services.actions.ai_action_audit import audit_ai_action
from audit.services import OperationLog
from identity.models import UserInfo

pytestmark = pytest.mark.django_db


def test_ai_action_audit_masks_sensitive_params():
    user = UserInfo.objects.create_user(username="ai-audit-user", password="Test@123456")
    audit_ai_action(
        user,
        "user.create",
        {"username": "new-user", "password": "PlainSecret123", "token": "tok-abc"},
        True,
        "ok",
    )
    row = OperationLog.objects.filter(module="AI:action").latest("created_time")
    changes = json.loads(row.changes)
    assert changes["params"]["username"] == "new-user"
    assert changes["params"]["password"] != "PlainSecret123"
    assert changes["params"]["token"] != "tok-abc"
    assert "PlainSecret123" not in row.changes
    assert "tok-abc" not in row.changes
