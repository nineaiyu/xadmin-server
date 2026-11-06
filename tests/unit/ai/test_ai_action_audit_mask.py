# -*- coding: utf-8 -*-
"""AI 动作审计不得把 password/token 等参数原文落库；creator 必须归集操作人。"""

import json

import pytest

from ai.services.actions.ai_action_audit import audit_ai_action, audit_ai_ask
from ai.services.assistant.ai_chat import persist_message
from ai.services.assistant.ai_guard import audit_ai_security
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


def test_ai_action_audit_records_creator():
    """AI 语义审计必须写 creator（操作人维度可筛选），object_pk 保留观测看板契约。"""
    user = UserInfo.objects.create_user(username="ai-audit-creator", password="Test@123456")
    audit_ai_action(user, "user.create", {}, True, "ok")
    row = OperationLog.objects.filter(module="AI:action").latest("created_time")
    assert row.creator_id == user.pk
    assert row.object_pk == str(user.pk)

    audit_ai_ask(user, "问题", True, "ok")
    ask_row = OperationLog.objects.filter(module="AI:ask").latest("created_time")
    assert ask_row.creator_id == user.pk


def test_ai_security_audit_records_creator():
    """AI 安全审计走统一收口后同样归集操作人。"""
    user = UserInfo.objects.create_user(username="ai-sec-creator", password="Test@123456")
    audit_ai_security(user, "prompt_injection", detail="ref", extra={"patterns": ["x"]})
    row = OperationLog.objects.filter(module="AI:security").latest("created_time")
    assert row.creator_id == user.pk
    assert row.status_code == 1001


def test_persist_message_masks_sensitive_extra():
    """助手消息 extra 落库前统一脱敏：动作结果携带的敏感键不落历史表。"""
    user = UserInfo.objects.create_user(username="ai-extra-mask", password="Test@123456")
    row = persist_message(
        user,
        "action",
        "assistant",
        content="done",
        extra={"action_result": {"password": "PlainSecret456", "name": "ok"}},
    )
    assert row is not None
    assert row.extra["action_result"]["password"] != "PlainSecret456"
    assert row.extra["action_result"]["name"] == "ok"
    assert "PlainSecret456" not in json.dumps(row.extra)
