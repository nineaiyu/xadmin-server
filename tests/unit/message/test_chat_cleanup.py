# -*- coding: utf-8 -*-
"""聊天历史自动清理测试（二期，CHAT_HISTORY_DAYS）。"""

import pytest
from django.utils import timezone

from message import chat as chat_service
from message.models import ChatMessage, ChatRoom

pytestmark = pytest.mark.django_db


def _make_message(room, user, content, days_ago: int):
    message, _created = chat_service.create_message(room, user, content)
    # update() 绕过 auto_now_add，直接把消息铺到历史时间点
    ChatMessage.objects.filter(pk=message.pk).update(created_time=timezone.now() - timezone.timedelta(days=days_ago))
    return message


@pytest.fixture
def room_with_history(superuser):
    room = chat_service.get_public_room()
    old = _make_message(room, superuser, "老消息", days_ago=40)
    fresh = _make_message(room, superuser, "新消息", days_ago=1)
    return room, old, fresh


class TestCleanExpiredHistory:
    def test_deletes_only_expired(self, room_with_history):
        room, old, fresh = room_with_history
        removed = chat_service.clean_expired_history(keep_days=30)
        assert removed == 1
        assert not ChatMessage.objects.filter(pk=old.pk).exists()
        assert ChatMessage.objects.filter(pk=fresh.pk).exists()
        # 会话与成员关系保留
        assert ChatRoom.objects.filter(pk=room.pk).exists()

    def test_keep_days_zero_is_noop(self, room_with_history):
        __, old, fresh = room_with_history
        assert chat_service.clean_expired_history(keep_days=0) == 0
        assert ChatMessage.objects.filter(pk__in=[old.pk, fresh.pk]).count() == 2

    def test_reads_chat_history_days_config(self, room_with_history, monkeypatch):
        """保留期读系统配置（单测里 patch SysConfig property，参照 test_export_record 范式）。"""
        from common.core.config import SysConfig

        __room, old, fresh = room_with_history
        monkeypatch.setattr(type(SysConfig), "CHAT_HISTORY_DAYS", property(lambda self: 30), raising=False)
        removed = chat_service.clean_expired_history()
        assert removed == 1
        assert not ChatMessage.objects.filter(pk=old.pk).exists()
        assert ChatMessage.objects.filter(pk=fresh.pk).exists()

    def test_period_task_runs_with_config(self, room_with_history, monkeypatch):
        from common.core.config import SysConfig
        from message.tasks import clean_chat_history_job

        __room, old, fresh = room_with_history
        monkeypatch.setattr(type(SysConfig), "CHAT_HISTORY_DAYS", property(lambda self: 30), raising=False)
        assert clean_chat_history_job() == 1
        assert not ChatMessage.objects.filter(pk=old.pk).exists()

    def test_period_task_registered_as_periodic(self):
        """任务必须以周期任务形式注册（beat 缺位时清理静默不执行，表会无限增长）。"""
        from message.tasks import clean_chat_history_job

        assert getattr(clean_chat_history_job, "delay", None) is not None
