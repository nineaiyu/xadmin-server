# -*- coding: utf-8 -*-
"""聊天室 WS consumer（ChatNotify）集成测试。

覆盖：准入（匿名 4401 / 无权限 4403 / 超管放行）、连接即下发未读快照、
公共与私聊广播拓扑、未读推送、client_msg_id 幂等、撤回/已读上行、
心跳只续期两个聊天组而不污染在线索引。
"""

import json

import pytest
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from message import chat as chat_service
from message.consumers import ChatNotify
from message.models import ChatMessage, ChatRoom
from message.utils import get_chat_user_group_name, get_public_chat_group_name

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


@pytest.fixture
def alice(db):
    from system.models import UserInfo

    return UserInfo.objects.create_user(username="alice", password="Test@123456", nickname="爱丽丝")


@pytest.fixture
def bob(db):
    from system.models import UserInfo

    return UserInfo.objects.create_user(username="bob", password="Test@123456", nickname="鲍勃")


def _make_consumer(ws_layer, user, channel="specific.chat-channel"):
    """构造不依赖真实连接的 consumer，捕获其对外发送。"""
    consumer = ChatNotify()
    consumer.channel_layer = ws_layer
    consumer.channel_name = channel
    consumer.scope = {"user": user}
    consumer.user = user
    consumer.group_name = get_chat_user_group_name(user.pk)
    consumer.public_group = get_public_chat_group_name()
    consumer.disconnected = False

    captured = []
    closed = []

    async def fake_send_base_json(action, data=None, mid=None, code=1000, detail=None, close=False, **kwargs):
        captured.append({"action": action, "data": data, "code": code, "detail": detail})

    async def fake_close(code=None):
        closed.append(code)

    consumer.send_base_json = fake_send_base_json
    consumer.close = fake_close
    return consumer, captured, closed


def _capture_group_send(ws_layer, monkeypatch):
    sent = []
    original = ws_layer.group_send

    async def recording_group_send(group, message):
        sent.append({"group": group, "message": message})
        return await original(group, message)

    monkeypatch.setattr(ws_layer, "group_send", recording_group_send)
    return sent


@pytest.fixture
def fast_close(monkeypatch):
    """连接拒绝/未知 action 分支会 sleep 3 秒再关闭，测试里去掉等待。"""

    async def fake_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr("message.consumers.asyncio.sleep", fake_sleep)


@pytest.fixture
def chat_push_enabled(db):
    """与正式库种子等价：站内信提醒开关可被用户继承（inherit=True）。

    测试库没有跑 load_init_json，缺这条时 `UserConfig(pk).PUSH_CHAT_MESSAGE`
    会回退成空 dict（视为关闭），提醒链路永远不触发。
    """
    from system.models import SystemConfig

    SystemConfig.objects.create(key="PUSH_CHAT_MESSAGE", value=True, inherit=True, access=True, is_active=True)
    yield


class TestConnect:
    def test_anonymous_rejected(self, ws_layer, fast_close):
        async def scenario():
            consumer = ChatNotify()
            consumer.channel_layer = ws_layer
            consumer.channel_name = "specific.anon"
            consumer.scope = {"user": None}
            consumer.user = None
            closed = []

            async def fake_close(code=None):
                closed.append(code)

            consumer.close = fake_close
            await consumer.connect()
            assert closed == [4401]

        async_to_sync(scenario)()

    def test_user_without_permission_rejected(self, ws_layer, normal_user):
        async def scenario():
            consumer, _captured, closed = _make_consumer(ws_layer, normal_user)
            await consumer.connect()
            assert closed == [4403]

        async_to_sync(scenario)()

    def test_superuser_joins_both_groups_and_gets_unread_snapshot(self, ws_layer, superuser, bob):
        room = chat_service.get_or_create_private_room(superuser, bob)
        chat_service.create_message(room, bob, "未读一条")
        chat_service.bump_unread(room, bob.pk)

        async def scenario():
            consumer, captured, __ = _make_consumer(ws_layer, superuser)
            accepted = []

            async def fake_accept():
                accepted.append(True)

            consumer.accept = fake_accept
            await consumer.connect()

            assert accepted == [True]
            assert consumer.channel_name in await ws_layer.get_layers(consumer.public_group)
            assert consumer.channel_name in await ws_layer.get_layers(consumer.group_name)
            # 未读快照（首屏红点）
            assert captured == [
                {"action": "chat_unread", "data": {"room_id": room.pk, "unread_count": 1}, "code": 1000, "detail": None}
            ]

        async_to_sync(scenario)()

    def test_ping_keeps_chat_groups_without_online_index(self, ws_layer, superuser):
        """聊天连接心跳只续期聊天组：不写入在线索引（在线列表不重复计数）。"""

        from message.utils import get_online_info

        async def scenario():
            consumer, captured, __ = _make_consumer(ws_layer, superuser)
            await ws_layer.group_add(consumer.public_group, consumer.channel_name)
            await ws_layer.group_add(consumer.group_name, consumer.channel_name)

            await consumer.receive(json.dumps({"action": "ping", "data": ""}))

            assert captured[0]["data"] == "pong"
            assert await ws_layer.get_online_user_pks() == []
            assert consumer.channel_name in await ws_layer.get_layers(consumer.public_group)
            assert consumer.channel_name in await ws_layer.get_layers(consumer.group_name)

        async_to_sync(scenario)()
        # 在线快照接口同样不应看到聊天连接（同步断言：避免在协程里查库）
        assert get_online_info() == ([], [])


class TestSendPublic:
    def test_public_message_broadcast_to_public_group(self, ws_layer, superuser, monkeypatch):
        room = chat_service.get_public_room()
        sent = _capture_group_send(ws_layer, monkeypatch)
        pushes = []

        async def fake_push(user_pk, message, **kwargs):
            pushes.append((user_pk, message))

        monkeypatch.setattr("message.consumers.async_push_message", fake_push)

        async def scenario():
            consumer, __, ___ = _make_consumer(ws_layer, superuser)
            await consumer.receive(
                json.dumps(
                    {
                        "action": "chat_message",
                        "data": {"room_id": room.pk, "content": "大家好", "client_msg_id": "c-public"},
                    }
                )
            )

        async_to_sync(scenario)()

        assert [item["group"] for item in sent] == [get_public_chat_group_name()]
        payload = sent[0]["message"]["data"]
        assert payload["content"] == "大家好"
        assert payload["room_type"] == ChatRoom.RoomType.PUBLIC
        assert ChatMessage.objects.filter(room=room).count() == 1
        assert pushes == []  # 无 @提及

    def test_mentions_push_all_targets_except_sender(
        self, ws_layer, superuser, alice, bob, monkeypatch, chat_push_enabled
    ):
        room = chat_service.get_public_room()
        sent = _capture_group_send(ws_layer, monkeypatch)
        pushes = []

        async def fake_push(user_pk, message, **kwargs):
            pushes.append((user_pk, message))

        monkeypatch.setattr("message.consumers.async_push_message", fake_push)

        async def scenario():
            consumer, __, ___ = _make_consumer(ws_layer, superuser)
            await consumer.handle_send(
                {"room_id": room.pk, "content": "请 @alice 和 @bob 看下 @alice", "client_msg_id": "c-mention"}
            )

        async_to_sync(scenario)()

        assert [item["group"] for item in sent] == [get_public_chat_group_name()]
        pushed_pks = sorted(pk for pk, __ in pushes)
        assert pushed_pks == sorted([alice.pk, bob.pk])
        assert all(message["message_type"] == "chat_message" for __, message in pushes)
        assert all(message["notice_type"]["value"] == 0 for __, message in pushes)

    def test_invalid_room_returns_readable_error(self, ws_layer, superuser):
        async def scenario():
            consumer, captured, __ = _make_consumer(ws_layer, superuser)
            await consumer.handle_send({"room_id": 999999, "content": "hi"})
            assert captured[0]["code"] == 1001

        async_to_sync(scenario)()

    def test_empty_content_rejected(self, ws_layer, superuser):
        room = chat_service.get_public_room()
        result = {}

        async def scenario():
            consumer, captured, __ = _make_consumer(ws_layer, superuser)
            await consumer.handle_send({"room_id": room.pk, "content": "   "})
            result["captured"] = captured

        async_to_sync(scenario)()
        assert result["captured"][0]["code"] == 1001
        assert ChatMessage.objects.filter(room=room).count() == 0


class TestSendPrivate:
    def test_private_message_targets_members_and_bumps_unread(
        self, ws_layer, alice, bob, monkeypatch, chat_push_enabled
    ):
        room = chat_service.get_or_create_private_room(alice, bob)
        sent = _capture_group_send(ws_layer, monkeypatch)
        pushes = []

        async def fake_push(user_pk, message, **kwargs):
            pushes.append((user_pk, message))

        monkeypatch.setattr("message.consumers.async_push_message", fake_push)

        async def scenario():
            consumer, __, ___ = _make_consumer(ws_layer, alice)
            await consumer.handle_send({"room_id": room.pk, "content": "私聊你好", "client_msg_id": "c-private"})

        async_to_sync(scenario)()

        groups = [item["group"] for item in sent if item["message"]["type"] == "chat_message"]
        assert sorted(groups) == sorted([get_chat_user_group_name(alice.pk), get_chat_user_group_name(bob.pk)])
        # 未读事件只推给接收者
        unread_events = [item for item in sent if item["message"]["type"] == "chat_unread"]
        assert len(unread_events) == 1
        assert unread_events[0]["group"] == get_chat_user_group_name(bob.pk)
        assert unread_events[0]["message"]["data"]["unread_count"] == 1
        # 对端未开聊天室页面 → 站内信提醒可达
        assert [pk for pk, __ in pushes] == [bob.pk]
        assert pushes[0][1]["message_type"] == "chat_private"
        assert pushes[0][1]["room_id"] == room.pk

    def test_private_message_skips_push_when_peer_in_chat(self, ws_layer, alice, bob, monkeypatch):
        room = chat_service.get_or_create_private_room(alice, bob)
        _capture_group_send(ws_layer, monkeypatch)
        pushes = []

        async def fake_push(user_pk, message, **kwargs):
            pushes.append(user_pk)

        monkeypatch.setattr("message.consumers.async_push_message", fake_push)

        async def scenario():
            # 对端聊天连接在线（页面打开）：消息已实时送达，不再重复弹站内信
            await ws_layer.group_add(get_chat_user_group_name(bob.pk), "specific.bob-chat")
            consumer, __, ___ = _make_consumer(ws_layer, alice)
            await consumer.handle_send({"room_id": room.pk, "content": "你在看吗"})

        async_to_sync(scenario)()
        assert pushes == []

    def test_client_msg_id_idempotent(self, ws_layer, alice, bob, monkeypatch):
        room = chat_service.get_or_create_private_room(alice, bob)
        sent = _capture_group_send(ws_layer, monkeypatch)

        async def fake_push(*args, **kwargs):
            return None

        monkeypatch.setattr("message.consumers.async_push_message", fake_push)

        async def scenario():
            consumer, captured, __ = _make_consumer(ws_layer, alice)
            await consumer.handle_send({"room_id": room.pk, "content": "重发一次", "client_msg_id": "same-id"})
            await consumer.handle_send({"room_id": room.pk, "content": "重发一次", "client_msg_id": "same-id"})

        async_to_sync(scenario)()

        # 只有一次真实广播（双方各一条 chat_message）+ 接收者一条未读事件；
        # 第二次命中幂等只回执给发送方，不产生任何 group_send
        assert ChatMessage.objects.filter(room=room).count() == 1
        assert len([item for item in sent if item["message"]["type"] == "chat_message"]) == 2
        assert len([item for item in sent if item["message"]["type"] == "chat_unread"]) == 1
        assert len(sent) == 3


class TestRecallAndRead:
    def test_recall_broadcasts_to_room(self, ws_layer, alice, bob, monkeypatch):
        room = chat_service.get_or_create_private_room(alice, bob)
        message, __ = chat_service.create_message(room, alice, "撤回我")
        sent = _capture_group_send(ws_layer, monkeypatch)

        async def scenario():
            consumer, ___, ____ = _make_consumer(ws_layer, alice)
            await consumer.handle_recall({"message_id": message.pk})

        async_to_sync(scenario)()

        assert [item["message"]["type"] for item in sent] == ["chat_recall", "chat_recall"]
        assert sent[0]["message"]["data"]["message_id"] == message.pk

    def test_recall_other_message_rejected(self, ws_layer, alice, bob):
        room = chat_service.get_or_create_private_room(alice, bob)
        message, __ = chat_service.create_message(room, bob, "别人的")

        async def scenario():
            consumer, captured, __ = _make_consumer(ws_layer, alice)
            await consumer.handle_recall({"message_id": message.pk})
            assert captured[0]["code"] == 1001

        async_to_sync(scenario)()

    def test_read_clears_unread_and_replies_cursor(self, ws_layer, alice, bob):
        room = chat_service.get_or_create_private_room(alice, bob)
        message, __ = chat_service.create_message(room, alice, "已读")
        chat_service.bump_unread(room, alice.pk)

        async def scenario():
            consumer, captured, __ = _make_consumer(ws_layer, bob)
            await consumer.handle_read({"room_id": room.pk})
            assert captured[0]["action"] == "chat_read"
            assert captured[0]["data"] == {"room_id": room.pk, "last_read_id": message.pk}

        async_to_sync(scenario)()
        from message.models import ChatRoomMember

        assert ChatRoomMember.objects.get(room=room, user=bob).unread_count == 0

    def test_unknown_action_closes(self, ws_layer, superuser, fast_close):
        async def scenario():
            consumer, __, closed = _make_consumer(ws_layer, superuser)
            consumer.disconnected = True
            await consumer.receive_json("nope", {}, {"action": "nope"})
            assert closed == [None]

        async_to_sync(scenario)()
