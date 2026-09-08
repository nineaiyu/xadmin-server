# -*- coding: utf-8 -*-
"""字段级审计 diff 集成测试（白名单模型的 update 写入 OperationLog.changes）。"""

import json

import pytest

from notifications.models import MessageContent
from system.models import OperationLog

pytestmark = pytest.mark.django_db

NOTICE_URL = "/api/notifications/notice-messages"


@pytest.fixture
def notice(db, superuser):
    return MessageContent.objects.create(title="审计测试", message="<p>v1</p>", notice_type=2)


def test_update_records_changes(auth_client, notice, settings, django_capture_on_commit_callbacks):
    settings.AUDIT_DIFF_MODELS = ["notifications.MessageContent"]
    with django_capture_on_commit_callbacks(execute=True):
        resp = auth_client.patch(f"{NOTICE_URL}/{notice.pk}", {"title": "审计测试v2"}, format="json")
    assert resp.status_code == 200, resp.data

    log = OperationLog.objects.filter(path__icontains="notice-messages", method="PATCH").latest("id")
    changes = json.loads(log.changes)
    assert changes["title"]["old"] == "审计测试"
    assert changes["title"]["new"] == "审计测试v2"


def test_whitelist_off_by_default(auth_client, notice, django_capture_on_commit_callbacks):
    """默认白名单为空：不记录 diff，也不额外查询。"""
    with django_capture_on_commit_callbacks(execute=True):
        resp = auth_client.patch(f"{NOTICE_URL}/{notice.pk}", {"title": "审计测试v3"}, format="json")
    assert resp.status_code == 200, resp.data

    log = OperationLog.objects.filter(path__icontains="notice-messages", method="PATCH").latest("id")
    assert not log.changes
