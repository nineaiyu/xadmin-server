# -*- coding: utf-8 -*-
"""聊天表情回应（chat_reaction）集成测试。

覆盖协议与 fail-closed 口径：
1. 上行 add/remove 落库到 ``extra["reactions"]``（无新表）并向房间广播全量表；
2. 幂等：重复 add 不重复记录、移除不存在的回应不产生写；
3. 越权：remove 只能移除自己（载荷无目标用户字段，替他人移除不生效）；
4. 静默忽略：消息不存在 / 已撤回 / 非房间成员 / 机器消息（ai / system）；
5. 校验回执：emoji 超长 / op 非法 / 回应数超上限 → 1001；
6. 并发：多线程同时回应同一消息，行锁保证 extra 读改写不丢更新。

广播拓扑与 chat_message 同源（公共房间 → 公共组；私聊 → 成员各自聊天组）。
并发（行锁不丢更新）用例依赖真实事务隔离，单独放在 test_chat_reaction_concurrency.py
（模块级 transaction=True，与 pytestmark=django_db 的事务包裹语义互斥）。
"""

import json

import pytest
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from message import chat as chat_service
from message.consumers import ChatNotify
from message.models import ChatMessage
from message.utils import get_chat_user_group_name, get_public_chat_group_name

pytestmark = pytest.mark.django_db


@pytest.fixture
def ws_layer():
    from tests.channel_layer import reset_layer_state

    layer = get_channel_layer()
    reset_layer_state(layer)
    yield layer
    reset_layer_state(layer)


@pytest.fixture
def alice(db):
    from system.models import UserInfo

    return UserInfo.objects.create_user(username="react-alice", password="Test@123456", nickname="爱丽丝")


@pytest.fixture
def bob(db):
    from system.models import UserInfo

    return UserInfo.objects.create_user(username="react-bob", password="Test@123456", nickname="鲍勃")


@pytest.fixture
def charlie(db):
    from system.models import UserInfo

    return UserInfo.objects.create_user(username="react-charlie", password="Test@123456", nickname="卡罗")


def _make_consumer(ws_layer, user, channel="specific.chat-reaction"):
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


def _react(ws_layer, user, data):
    """驱动一次上行 chat_reaction（不依赖真实连接），返回 (consumer, captured)。"""
    consumer, captured, __ = _make_consumer(ws_layer, user)

    async def scenario():
        await consumer.receive(json.dumps({"action": "chat_reaction", "data": data}))

    async_to_sync(scenario)()
    return consumer, captured


class TestReactionPersistence:
    def test_add_persists_and_broadcasts_full_table(self, ws_layer, alice, monkeypatch):
        room = chat_service.get_public_room()
        message, __ = chat_service.create_message(room, alice, "回应我")
        sent = _capture_group_send(ws_layer, monkeypatch)

        __, captured = _react(ws_layer, alice, {"message": message.pk, "emoji": "👍", "op": "add"})

        message.refresh_from_db()
        assert message.extra["reactions"] == {"👍": [alice.pk]}
        assert captured == [], "成功路径不向发送方回错误帧"
        assert [item["group"] for item in sent] == [get_public_chat_group_name()], "公共房间走公共广播组"
        frame = sent[0]["message"]
        assert frame["type"] == "chat_reaction"
        payload = frame["data"]
        # 下行全量表形状：{room, message, reactions, ts}（客户端整体替换）
        assert payload["room"] == room.pk
        assert payload["message"] == message.pk
        assert payload["reactions"] == {"👍": [alice.pk]}
        assert isinstance(payload["ts"], int) and payload["ts"] > 0

    def test_private_room_reaches_member_groups(self, ws_layer, alice, bob, monkeypatch):
        room = chat_service.get_or_create_private_room(alice, bob)
        message, __ = chat_service.create_message(room, alice, "私聊回应")
        sent = _capture_group_send(ws_layer, monkeypatch)

        _react(ws_layer, alice, {"message": message.pk, "emoji": "🎉", "op": "add"})

        assert sorted(item["group"] for item in sent) == sorted(
            [get_chat_user_group_name(alice.pk), get_chat_user_group_name(bob.pk)]
        )

    def test_add_is_idempotent(self, ws_layer, alice, monkeypatch):
        room = chat_service.get_public_room()
        message, __ = chat_service.create_message(room, alice, "重复回应")
        _capture_group_send(ws_layer, monkeypatch)

        _react(ws_layer, alice, {"message": message.pk, "emoji": "👍", "op": "add"})
        _react(ws_layer, alice, {"message": message.pk, "emoji": "👍", "op": "add"})

        message.refresh_from_db()
        assert message.extra["reactions"] == {"👍": [alice.pk]}, "重复 add 不重复记录"

    def test_remove_keeps_others_then_clears_key(self, ws_layer, alice, bob):
        room = chat_service.get_public_room()
        message, __ = chat_service.create_message(room, alice, "多人回应")
        chat_service.toggle_reaction(alice, message.pk, "👍", "add")
        chat_service.toggle_reaction(bob, message.pk, "👍", "add")

        chat_service.toggle_reaction(alice, message.pk, "👍", "remove")
        message.refresh_from_db()
        assert message.extra["reactions"] == {"👍": [bob.pk]}, "remove 只移除自己"

        chat_service.toggle_reaction(bob, message.pk, "👍", "remove")
        message.refresh_from_db()
        assert "reactions" not in (message.extra or {}), "回应清空后移除键，extra 不留空壳"

    def test_remove_cannot_target_other_users(self, ws_layer, alice, bob, monkeypatch):
        """载荷无目标用户语义：多传 user 字段也不能替他人移除。"""
        room = chat_service.get_public_room()
        message, __ = chat_service.create_message(room, alice, "不许替我移除")
        chat_service.toggle_reaction(bob, message.pk, "👍", "add")
        _capture_group_send(ws_layer, monkeypatch)

        __, captured = _react(ws_layer, alice, {"message": message.pk, "emoji": "👍", "op": "remove", "user": bob.pk})

        message.refresh_from_db()
        assert message.extra["reactions"] == {"👍": [bob.pk]}
        assert captured == []

    def test_reactions_survive_in_history_payload(self, ws_layer, alice):
        """历史/广播载荷（message_payload）随 extra 携带回应表。"""
        room = chat_service.get_public_room()
        message, __ = chat_service.create_message(room, alice, "历史回应")
        chat_service.toggle_reaction(alice, message.pk, "👍", "add")
        message.refresh_from_db()  # toggle 走 DB 行更新，内存实例需刷新后再取载荷
        payload = chat_service.message_payload(message, room=room)
        assert payload["extra"]["reactions"] == {"👍": [alice.pk]}


class TestReactionSilentIgnore:
    """fail-closed：无效目标一律静默忽略（不落库、不广播、不回错误帧）。"""

    def test_missing_message_ignored(self, ws_layer, alice, monkeypatch):
        _capture_group_send(ws_layer, monkeypatch)
        __, captured = _react(ws_layer, alice, {"message": 999999, "emoji": "👍", "op": "add"})
        assert captured == []

    def test_non_int_message_ignored(self, ws_layer, alice, monkeypatch):
        _capture_group_send(ws_layer, monkeypatch)
        __, captured = _react(ws_layer, alice, {"message": "not-a-pk", "emoji": "👍", "op": "add"})
        assert captured == []

    def test_recalled_message_ignored(self, ws_layer, alice, monkeypatch):
        room = chat_service.get_public_room()
        message, __ = chat_service.create_message(room, alice, "撤回后不可回应")
        chat_service.recall_message(alice, message.pk)
        _capture_group_send(ws_layer, monkeypatch)

        __, captured = _react(ws_layer, alice, {"message": message.pk, "emoji": "👍", "op": "add"})

        assert captured == []
        message.refresh_from_db()
        assert "reactions" not in (message.extra or {})

    def test_non_member_ignored(self, ws_layer, alice, bob, charlie, monkeypatch):
        """非私聊房间成员：与 accessible_room 同口径忽略（不区分不存在与无权）。"""
        room = chat_service.get_or_create_private_room(alice, bob)
        message, __ = chat_service.create_message(room, alice, "成员才可回应")
        _capture_group_send(ws_layer, monkeypatch)

        __, captured = _react(ws_layer, charlie, {"message": message.pk, "emoji": "👍", "op": "add"})

        assert captured == []
        message.refresh_from_db()
        assert "reactions" not in (message.extra or {})

    @pytest.mark.parametrize("message_type", [ChatMessage.MessageType.AI, ChatMessage.MessageType.SYSTEM])
    def test_machine_messages_ignored(self, ws_layer, alice, message_type, monkeypatch):
        """ai / system 为机器生成消息，回应没有对象语义：静默忽略。"""
        room = chat_service.get_public_room()
        message, __ = chat_service.create_message(
            room,
            None,
            "机器消息",
            message_type=message_type,  # type: ignore[arg-type]
        )
        _capture_group_send(ws_layer, monkeypatch)

        __, captured = _react(ws_layer, alice, {"message": message.pk, "emoji": "👍", "op": "add"})

        assert captured == []
        message.refresh_from_db()
        assert "reactions" not in (message.extra or {})


class TestReactionValidation:
    def test_empty_emoji_rejected(self, ws_layer, alice):
        room = chat_service.get_public_room()
        message, __ = chat_service.create_message(room, alice, "hi")
        __, captured = _react(ws_layer, alice, {"message": message.pk, "emoji": "   ", "op": "add"})
        assert captured[0]["code"] == 1001

    def test_too_long_emoji_rejected(self, ws_layer, alice):
        from message.models import REACTION_EMOJI_MAX_LENGTH

        room = chat_service.get_public_room()
        message, __ = chat_service.create_message(room, alice, "hi")
        __, captured = _react(
            ws_layer, alice, {"message": message.pk, "emoji": "a" * (REACTION_EMOJI_MAX_LENGTH + 1), "op": "add"}
        )
        assert captured[0]["code"] == 1001

    def test_stripped_emoji_normalized(self, ws_layer, alice):
        """emoji 去首尾空白后判定与存储：' 👍 ' 与 '👍' 同键。"""
        room = chat_service.get_public_room()
        message, __ = chat_service.create_message(room, alice, "hi")
        chat_service.toggle_reaction(alice, message.pk, "👍", "add")
        chat_service.toggle_reaction(alice, message.pk, " 👍 ", "add")
        message.refresh_from_db()
        assert message.extra["reactions"] == {"👍": [alice.pk]}

    def test_invalid_op_rejected(self, ws_layer, alice):
        room = chat_service.get_public_room()
        message, __ = chat_service.create_message(room, alice, "hi")
        __, captured = _react(ws_layer, alice, {"message": message.pk, "emoji": "👍", "op": "toggle"})
        assert captured[0]["code"] == 1001

    def test_max_emoji_keys_enforced(self, ws_layer, alice, monkeypatch):
        # 上限常量经 chat_service 按值导入，需 patch 使用方模块的绑定
        monkeypatch.setattr("message.chat.REACTION_MAX_EMOJI_KEYS", 2)
        room = chat_service.get_public_room()
        message, __ = chat_service.create_message(room, alice, "emoji 键上限")
        for emoji in ("👍", "🎉"):
            chat_service.toggle_reaction(alice, message.pk, emoji, "add")

        __, captured = _react(ws_layer, alice, {"message": message.pk, "emoji": "🚀", "op": "add"})
        assert captured[0]["code"] == 1001, "第 21 个 emoji 键（此处 monkeypatch 为 3）被拒"
        # 既有键继续 add 仍可用（上限只约束键数量，不影响已有回应）
        __, captured = _react(ws_layer, alice, {"message": message.pk, "emoji": "🎉", "op": "add"})
        assert captured == []

    def test_max_users_per_emoji_enforced(self, ws_layer, alice, monkeypatch):
        monkeypatch.setattr("message.chat.REACTION_MAX_USERS_PER_EMOJI", 1)
        room = chat_service.get_public_room()
        message, __ = chat_service.create_message(room, alice, "单 emoji 用户上限")
        chat_service.toggle_reaction(alice, message.pk, "👍", "add")

        __, captured = _react(ws_layer, alice, {"message": message.pk, "emoji": "👍", "op": "add"})
        # 同一用户重复 add 幂等放行（无新增成员）
        assert captured == []

        from system.models import UserInfo

        bob = UserInfo.objects.create_user(username="react-cap-bob", password="Test@123456")
        __, captured = _react(ws_layer, bob, {"message": message.pk, "emoji": "👍", "op": "add"})
        assert captured[0]["code"] == 1001, "超上限（此处 monkeypatch 为 1）的新增回应被拒"


class TestReactionRateLimit:
    def test_reaction_shares_send_rate_limit(self, ws_layer, monkeypatch):
        """回应与消息发送共用每用户每秒限流：超限时回执 1001 且不落库不广播。"""
        from system.models import UserInfo

        monkeypatch.setattr("message.consumers.CHAT_SEND_LIMIT_PER_SECOND", 1)
        user = UserInfo.objects.create_user(username="react-rate", password="Test@123456")
        room = chat_service.get_public_room()
        message, __ = chat_service.create_message(room, user, "限流目标")
        _capture_group_send(ws_layer, monkeypatch)

        async def scenario():
            consumer, captured, __ = _make_consumer(ws_layer, user)
            for index in range(3):
                await consumer.receive(
                    json.dumps(
                        {
                            "action": "chat_reaction",
                            "data": {"message": message.pk, "emoji": f"{'👍' * (index + 1)}", "op": "add"},
                        }
                    )
                )
            return captured

        captured = async_to_sync(scenario)()
        message.refresh_from_db()
        assert len((message.extra or {}).get("reactions") or {}) == 1, "只有第一条落库"
        rejected = [item for item in captured if item["code"] == 1001]
        assert len(rejected) == 2
        assert rejected[0]["detail"]


class TestReactionDownlink:
    def test_consumer_downlink_handler_sends_frame(self, ws_layer, alice):
        """下行事件经 chat_reaction handler 原样透传（action 与协议对齐）。"""
        consumer, captured, __ = _make_consumer(ws_layer, alice)
        payload = {"room": 1, "message": 2, "reactions": {"👍": [alice.pk]}, "ts": 1727700000}

        async def scenario():
            await consumer.chat_reaction({"type": "chat_reaction", "data": payload})

        async_to_sync(scenario)()
        assert captured == [{"action": "chat_reaction", "data": payload, "code": 1000, "detail": None}]
