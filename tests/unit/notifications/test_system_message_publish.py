# -*- coding: utf-8 -*-
"""SystemMessage 异步发布序列化守护测试。

回归背景：软删除模型的 related manager 返回 SoftDeleteQuerySet，
`subscription.users.values_list("pk", flat=True)` 的结果若未物化为 list，
直接传给 celery `publish_task.delay` 会因 kombu JSON 序列化失败抛
EncodeError（SoftDeleteQuerySet is not JSON serializable），
导致敏感操作告警静默失败。
"""

import json
from unittest import mock

import pytest

from notifications.notifications import publish_task
from system.notifications import SensitiveOperationMessage

pytestmark = pytest.mark.django_db


class TestAsyncPublishSerializable:
    def test_publish_passes_json_serializable_args(self, superuser):
        msg = SensitiveOperationMessage(
            {
                "module": "test",
                "path": "/api/system/user/1",
                "method": "DELETE",
                "ipaddress": "127.0.0.1",
                "created_time": "2026-09-09 17:08:40",
            }
        )
        with mock.patch.object(publish_task, "delay") as fake_delay:
            msg.publish(is_async=True)

        assert fake_delay.called
        args, kwargs = fake_delay.call_args
        # receive_user_ids 必须是 list（而非 QuerySet），且整体参数可 JSON 序列化
        assert isinstance(args[0], list)
        assert len(args[0]) == 1
        json.dumps(args)
        json.dumps(kwargs)
