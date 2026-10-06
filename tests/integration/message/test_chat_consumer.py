# -*- coding: utf-8 -*-
"""聊天室 WS consumer（ChatNotify）集成测试。

覆盖：准入（匿名 4401 / 无权限 4403 / 超管放行）、连接即下发未读快照、
公共与私聊广播拓扑、未读推送、client_msg_id 幂等、已读上行、
心跳只续期两个聊天组而不污染在线索引；撤回上行已收敛到 REST，
WS 上行 chat_recall 按未知 action 关闭。
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
    from tests.channel_layer import reset_layer_state

    layer = get_channel_layer()
    reset_layer_state(layer)
    yield layer
    reset_layer_state(layer)


@pytest.fixture
def alice(db):
    from identity.models import UserInfo

    return UserInfo.objects.create_user(username="alice", password="Test@123456", nickname="爱丽丝")


@pytest.fixture
def bob(db):
    from identity.models import UserInfo

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

        assert [item["group"] for item in sent] == [
            get_public_chat_group_name(),
            get_chat_user_group_name(superuser.pk),
        ]
        payload = sent[0]["message"]["data"]
        assert payload["content"] == "大家好"
        assert payload["room_type"] == ChatRoom.RoomType.PUBLIC
        # 共享广播帧不带撤回资格（按观看者计），发送者定向帧才携带
        assert "can_recall" not in payload
        assert sent[1]["message"]["data"]["can_recall"] is True
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

        assert [item["group"] for item in sent] == [
            get_public_chat_group_name(),
            get_chat_user_group_name(superuser.pk),
        ]
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
        # 共享广播到双方聊天组，发送者定向帧（携带 can_recall）再入 alice 组
        assert groups.count(get_chat_user_group_name(alice.pk)) == 2
        assert groups.count(get_chat_user_group_name(bob.pk)) == 1
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

        # 只有一次真实广播（双方各一条 chat_message + 发送者定向帧）+ 接收者一条
        # 未读事件；第二次命中幂等只回执给发送方，不产生任何 group_send
        assert ChatMessage.objects.filter(room=room).count() == 1
        assert len([item for item in sent if item["message"]["type"] == "chat_message"]) == 3
        assert len([item for item in sent if item["message"]["type"] == "chat_unread"]) == 1
        assert len(sent) == 4


class TestGroupFanoutBatch:
    """群聊扇出批量预取：一条群消息的在线判定与偏好读取各一次批量往返。"""

    @pytest.fixture
    def group_room(self, alice):
        from identity.models import UserInfo
        from message.models import ChatRoomMember, group_room_key

        members = [
            UserInfo.objects.create_user(username=f"fanout{index}", password="Test@123456") for index in range(6)
        ]
        room = ChatRoom.objects.create(
            room_type=ChatRoom.RoomType.GROUP, room_key=group_room_key(), name="扇出群", owner=alice
        )
        ChatRoomMember.objects.create(room=room, user=alice)
        for user in members:
            ChatRoomMember.objects.create(room=room, user=user)
        return room, members

    def test_single_batch_query_for_layer_and_preference(
        self, ws_layer, alice, group_room, monkeypatch, chat_push_enabled
    ):
        from message import consumers as consumers_module

        room, members = group_room
        calls = {"layers": 0, "groups": [], "pref": 0}
        original = ws_layer.get_layers_for_groups

        async def counting_batch(groups):
            calls["layers"] += 1
            calls["groups"] = list(groups)
            return await original(groups)

        monkeypatch.setattr(ws_layer, "get_layers_for_groups", counting_batch)

        def counting_pref(pks):
            calls["pref"] += 1
            return {pk: True for pk in pks}

        monkeypatch.setattr(consumers_module, "batch_push_chat_enabled", counting_pref)
        pushes = []

        async def fake_push(user_pk, message, **kwargs):
            pushes.append(user_pk)

        monkeypatch.setattr("message.consumers.async_push_message", fake_push)

        async def scenario():
            consumer, __, ___ = _make_consumer(ws_layer, alice)
            await consumer.handle_send({"room_id": room.pk, "content": "群消息", "client_msg_id": "c-group-batch"})

        async_to_sync(scenario)()

        # 一次批量在线判定（覆盖全体其他成员组）+ 一次批量偏好读取（非逐人 2N 次）
        assert calls["layers"] == 1
        assert calls["pref"] == 1
        assert len(calls["groups"]) == len(members)
        assert sorted(pushes) == sorted(user.pk for user in members)

    def test_online_member_skipped_without_push(self, ws_layer, alice, group_room, monkeypatch, chat_push_enabled):
        room, members = group_room
        online_user = members[0]
        pushes = []

        async def fake_push(user_pk, message, **kwargs):
            pushes.append(user_pk)

        monkeypatch.setattr("message.consumers.async_push_message", fake_push)

        async def scenario():
            # 该成员聊天室页面在线（连接在其个人聊天组）：实时可达，不再重复弹站内信
            await ws_layer.group_add(get_chat_user_group_name(online_user.pk), "specific.online-member")
            consumer, __, ___ = _make_consumer(ws_layer, alice)
            await consumer.handle_send({"room_id": room.pk, "content": "群消息2", "client_msg_id": "c-group-online"})

        async_to_sync(scenario)()

        assert online_user.pk not in pushes
        assert sorted(pushes) == sorted(user.pk for user in members[1:])

    def test_preference_off_skips_target(self, ws_layer, alice, group_room, monkeypatch, chat_push_enabled):
        from message import consumers as consumers_module

        room, members = group_room
        muted = members[-1]
        pushes = []

        async def fake_push(user_pk, message, **kwargs):
            pushes.append(user_pk)

        monkeypatch.setattr("message.consumers.async_push_message", fake_push)

        original_batch = consumers_module.batch_push_chat_enabled

        def with_muted(pks):
            return {pk: pk != muted.pk for pk in original_batch(pks)}

        monkeypatch.setattr(consumers_module, "batch_push_chat_enabled", with_muted)

        async def scenario():
            consumer, __, ___ = _make_consumer(ws_layer, alice)
            await consumer.handle_send({"room_id": room.pk, "content": "群消息3", "client_msg_id": "c-group-mute"})

        async_to_sync(scenario)()

        assert muted.pk not in pushes
        assert sorted(pushes) == sorted(user.pk for user in members[:-1])


class TestRecallAndRead:
    def test_recall_uplink_removed_closes_as_unknown(self, ws_layer, superuser, fast_close):
        """撤回上行唯一入口是 REST：chat_recall 上行帧按未知 action 处理（提示后关闭）。"""
        room = chat_service.get_public_room()
        message, __ = chat_service.create_message(room, superuser, "撤回不再走 WS 上行")

        async def scenario():
            consumer, __, closed = _make_consumer(ws_layer, superuser)
            consumer.disconnected = True
            await consumer.receive(json.dumps({"action": "chat_recall", "data": {"message_id": message.pk}}))
            assert closed == [None]

        async_to_sync(scenario)()
        message.refresh_from_db()
        assert message.is_recalled is False  # 上行不再触达撤回逻辑

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


class TestSendRateLimit:
    """WS 上行发送限流（每用户每秒 N 条）：恶意连接高频灌消息会放大成 DB 写与推送风暴。"""

    def test_over_limit_rejected_without_persisting(self, ws_layer, superuser, monkeypatch):
        monkeypatch.setattr("message.consumers.CHAT_SEND_LIMIT_PER_SECOND", 2)
        room = chat_service.get_public_room()
        sent = _capture_group_send(ws_layer, monkeypatch)

        async def scenario():
            consumer, captured, __ = _make_consumer(ws_layer, superuser)
            for index in range(4):
                await consumer.receive(
                    json.dumps(
                        {
                            "action": "chat_message",
                            "data": {
                                "room_id": room.pk,
                                "content": f"刷屏 {index}",
                                "client_msg_id": f"c-rate-{index}",
                            },
                        }
                    )
                )
            return captured

        captured = async_to_sync(scenario)()

        # 前两条放行，后续回执 1001（可读错误）且不落库、不广播；
        # 每条放行消息 = 公共广播帧 + 发送者定向帧
        assert ChatMessage.objects.filter(room=room).count() == 2
        assert len(sent) == 4
        rejected = [item for item in captured if item["code"] == 1001]
        assert len(rejected) == 2
        assert rejected[0]["detail"]

    def test_limit_is_per_user(self, ws_layer, alice, bob, monkeypatch):
        monkeypatch.setattr("message.consumers.CHAT_SEND_LIMIT_PER_SECOND", 1)
        room = chat_service.get_public_room()
        _capture_group_send(ws_layer, monkeypatch)

        async def scenario():
            for index, user in enumerate((alice, bob)):
                consumer, captured, __ = _make_consumer(ws_layer, user, channel=f"specific.chat-{index}")
                await consumer.receive(
                    json.dumps(
                        {
                            "action": "chat_message",
                            "data": {"room_id": room.pk, "content": f"你好 {index}", "client_msg_id": f"c-per-{index}"},
                        }
                    )
                )
                # 另一用户不受影响（限流按用户维度，不串号）
                assert not [item for item in captured if item["code"] == 1001]

        async_to_sync(scenario)()
        assert ChatMessage.objects.filter(room=room).count() == 2


class TestRecallFlagBroadcast:
    """新消息撤回资格下发：房间共享广播帧不带 can_recall（撤回资格按观看者计），
    发送者定向帧携带该字段，且与 REST 历史下发的判定（can_recall_for）完全一致。"""

    @staticmethod
    def _mute_push(monkeypatch):
        async def fake_push(*args, **kwargs):
            return None

        monkeypatch.setattr("message.consumers.async_push_message", fake_push)

    def test_sender_frame_carries_field_public_shared_frame_does_not(self, ws_layer, superuser, monkeypatch):
        room = chat_service.get_public_room()
        sent = _capture_group_send(ws_layer, monkeypatch)
        self._mute_push(monkeypatch)

        async def scenario():
            consumer, __, ___ = _make_consumer(ws_layer, superuser)
            await consumer.handle_send({"room_id": room.pk, "content": "大家好呀", "client_msg_id": "c-recall-flag"})

        async_to_sync(scenario)()

        frames = [item for item in sent if item["message"]["type"] == "chat_message"]
        assert [item["group"] for item in frames] == [
            get_public_chat_group_name(),
            get_chat_user_group_name(superuser.pk),
        ]
        assert "can_recall" not in frames[0]["message"]["data"]
        assert frames[1]["message"]["data"]["can_recall"] is True

    def test_sender_frame_matches_rest_history_criteria(self, ws_layer, alice, bob, monkeypatch):
        """定向帧的 can_recall 与 REST 历史（history_messages）同判定：撤回后双双翻 False。"""
        room = chat_service.get_or_create_private_room(alice, bob)
        sent = _capture_group_send(ws_layer, monkeypatch)
        self._mute_push(monkeypatch)

        async def scenario():
            consumer, __, ___ = _make_consumer(ws_layer, alice)
            await consumer.handle_send({"room_id": room.pk, "content": "可撤回", "client_msg_id": "c-recall-parity"})

        async_to_sync(scenario)()

        alice_frames = [
            item["message"]["data"]
            for item in sent
            if item["message"]["type"] == "chat_message" and item["group"] == get_chat_user_group_name(alice.pk)
        ]
        # alice 组先收到共享广播帧（不带字段），后收到发送者定向帧（携带）
        assert "can_recall" not in alice_frames[0]
        assert alice_frames[1]["can_recall"] is True

        message = ChatMessage.objects.get(room=room, client_msg_id="c-recall-parity")
        row = next(item for item in chat_service.history_messages(room, alice)["results"] if item["id"] == message.pk)
        assert row["can_recall"] is True
        # 接收者视角：REST 历史同样下发 False（非本人不可撤回）
        row_for_bob = next(
            item for item in chat_service.history_messages(room, bob)["results"] if item["id"] == message.pk
        )
        assert row_for_bob["can_recall"] is False

        chat_service.recall_message(alice, message.pk)
        message.refresh_from_db()  # recall_message 锁行重写，本实例需回读最新撤回态
        assert chat_service.can_recall_for(message, alice) is False
        row = next(item for item in chat_service.history_messages(room, alice)["results"] if item["id"] == message.pk)
        assert row["can_recall"] is False
