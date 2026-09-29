# -*- coding: utf-8 -*-
"""历史 ws/message 通道的 chat_message 直广播默认关闭（fail-closed）。

该路径无落库、无内容校验、无权限校验（握手允许落到共享组 message_system_default_0），
前端已切换 ws/chat；默认关闭避免未校验帧注入，仅在显式开启时保留老客户端兼容。
"""

import pytest
from asgiref.sync import async_to_sync

from message.notify import MessageNotify

pytestmark = pytest.mark.django_db


class _FakeLayer:
    def __init__(self):
        self.sent = []

    async def group_send(self, group, message):
        self.sent.append((group, message))


class _FakeUser:
    pk = 1
    username = "legacy-user"


def _consumer():
    """构造带桩的 consumer（不建立真实连接）。"""

    async def _noop(*args, **kwargs):
        return None

    consumer = MessageNotify(scope={"user": _FakeUser()}, receive=_noop, send=_noop)
    consumer.user = _FakeUser()
    consumer.group_name = "message_system_default_0"
    consumer.channel_layer = _FakeLayer()
    return consumer


def _send(consumer, data):
    async_to_sync(consumer.receive_json)("chat_message", data, "")


class TestLegacyGate:
    def test_disabled_by_default_rejects_and_closes(self, settings):
        """默认关闭：不广播、连接关闭（闭合「未校验帧注入共享组」面）。"""
        settings.CHAT_LEGACY_WS_BROADCAST_ENABLED = False
        consumer = _consumer()
        closed = []
        consumer.close = lambda *args, **kwargs: closed.append(True) or _async_none()

        _send(consumer, {"text": "hi"})
        assert consumer.channel_layer.sent == []
        assert closed, "关闭开关时连接应被关闭"

    def test_enabled_keeps_legacy_broadcast(self, settings, monkeypatch):
        """显式开启：维持历史行为（向房间组广播 + @ 提醒）。"""
        settings.CHAT_LEGACY_WS_BROADCAST_ENABLED = True
        notified = []

        async def _fake_notify(data, username):
            notified.append((data, username))

        monkeypatch.setattr("message.notify.notify_at_user_msg", _fake_notify)
        consumer = _consumer()
        _send(consumer, {"text": "hello", "pk": 999})

        assert len(consumer.channel_layer.sent) == 1
        group, message = consumer.channel_layer.sent[0]
        assert group == "message_system_default_0"
        assert message["type"] == "chat_message"
        # 发送者身份由服务端回填（忽略客户端自报的 pk）
        assert message["data"]["pk"] == _FakeUser.pk
        assert message["data"]["username"] == _FakeUser.username
        assert notified

    def test_switch_default_is_off(self):
        """配置默认值必须是关（历史路径不得默认开放）。"""
        from server.conf import Config

        assert Config.defaults["CHAT_LEGACY_WS_BROADCAST_ENABLED"] is False


async def _async_none():
    return None
