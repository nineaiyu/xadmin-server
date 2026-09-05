# -*- coding: utf-8 -*-
"""PERF-14：心跳直收测试。

旧实现 receive() 把 ping 投进 channel layer 队列（2 条 Redis 命令），
再由 consumer 收回处理；现在 receive() 直接调用 self.ping()，
心跳不再产生额外 Redis 往返，且在线索引随心跳续期。
"""
import json

import pytest
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from message.notify import MessageNotify
from message.utils import get_user_layer_group_name

pytestmark = pytest.mark.django_db


@pytest.fixture
def ws_layer():
    layer = get_channel_layer()
    layer._online_users = {}
    if hasattr(layer, "groups") and hasattr(layer.groups, "clear"):
        layer.groups.clear()
    yield layer
    layer._online_users = {}
    if hasattr(layer, "groups") and hasattr(layer.groups, "clear"):
        layer.groups.clear()


def _make_consumer(ws_layer, superuser):
    """构造一个不依赖真实 WebSocket 连接的 consumer，捕获其对外发送。"""
    consumer = MessageNotify()
    consumer.channel_layer = ws_layer
    consumer.channel_name = "specific.test-channel"
    consumer.scope = {
        "user": superuser,
        "url_route": {"kwargs": {"username": superuser.username, "group_name": ""}},
    }
    consumer.disconnected = False
    consumer.group_name = get_user_layer_group_name(superuser.pk)

    captured = []

    async def fake_send_base_json(action, data=None, mid=None, code=1000, detail=None, close=False, **kwargs):
        captured.append({"action": action, "data": data, "mid": mid})

    consumer.send_base_json = fake_send_base_json
    return consumer, captured


def test_ping_replies_pong_and_keeps_online(ws_layer, superuser):

    async def scenario():
        consumer, captured = _make_consumer(ws_layer, superuser)
        await ws_layer.group_add(consumer.group_name, consumer.channel_name)

        await consumer.receive(json.dumps({"action": "ping", "data": ""}))

        assert captured == [{"action": "ping", "data": "pong", "mid": None}]
        # 心跳后进入在线索引（反向索引随心跳续期）
        assert await ws_layer.get_online_user_pks() == [superuser.pk]
        assert await ws_layer.get_layers(consumer.group_name) == [consumer.channel_name]

    async_to_sync(scenario)()


def test_ping_with_mid_is_answered(ws_layer, superuser):

    async def scenario():
        consumer, captured = _make_consumer(ws_layer, superuser)
        await consumer.receive(json.dumps({"action": "ping", "data": "", "mid": "mid-1"}))
        assert captured[0]["data"] == "pong"
        assert captured[0]["mid"] == "mid-1"

    async_to_sync(scenario)()


def test_ping_no_longer_routes_through_channel_queue(ws_layer, superuser, monkeypatch):
    """ping 不再调用 channel_layer.send（旧实现每心跳多 2 条 Redis 命令）"""

    async def scenario():
        consumer, _captured = _make_consumer(ws_layer, superuser)
        await ws_layer.group_add(consumer.group_name, consumer.channel_name)

        sends = []
        original_send = ws_layer.send

        async def counting_send(channel, message):
            sends.append(message)
            return await original_send(channel, message)

        monkeypatch.setattr(ws_layer, "send", counting_send)

        await consumer.receive(json.dumps({"action": "ping", "data": ""}))
        # 直接处理：不应有任何向 channel 队列的投递
        assert sends == []

    async_to_sync(scenario)()


def test_userinfo_still_routes_through_channel(ws_layer, superuser, monkeypatch):
    """非心跳动作保持原有行为：仍经 channel layer 队列投递"""

    async def scenario():
        consumer, _captured = _make_consumer(ws_layer, superuser)
        await ws_layer.group_add(consumer.group_name, consumer.channel_name)

        sends = []
        original_send = ws_layer.send

        async def counting_send(channel, message):
            sends.append(message)
            return await original_send(channel, message)

        monkeypatch.setattr(ws_layer, "send", counting_send)

        await consumer.receive(json.dumps({"action": "userinfo", "data": ""}))
        assert sends and sends[0]["type"] == "userinfo"

    async_to_sync(scenario)()
