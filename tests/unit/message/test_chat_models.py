# -*- coding: utf-8 -*-
"""聊天室领域逻辑单测：会话幂等 / 消息幂等 / 未读游标 / 撤回 / @提及 / 准入。

覆盖口径：
1. room_key 规范化保证 (A,B) 与 (B,A) 同一会话；
2. client_msg_id 幂等：断线重发不产生第二行；
3. 未读计数与已读游标；公共房间不维护未读；
4. 撤回仅本人 + 2 分钟窗口；
5. 可访问性 fail-closed（非成员私聊、他人 AI 会话一律「不存在」）；
6. @提及全位置多目标解析。
"""

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone

from message import chat as chat_service
from message.models import ChatMessage, ChatRoom, private_room_key

pytestmark = pytest.mark.django_db


@pytest.fixture
def alice(db):
    from system.models import UserInfo

    return UserInfo.objects.create_user(username="alice", password="Test@123456", nickname="爱丽丝")


@pytest.fixture
def bob(db):
    from system.models import UserInfo

    return UserInfo.objects.create_user(username="bob", password="Test@123456", nickname="鲍勃")


class TestRoomKey:
    def test_private_room_key_is_order_independent(self):
        assert private_room_key(5, 3) == private_room_key(3, 5) == "dm:3:5"

    def test_private_room_reused_for_both_sides(self, alice, bob):
        first = chat_service.get_or_create_private_room(alice, bob)
        second = chat_service.get_or_create_private_room(bob, alice)
        assert first.pk == second.pk
        assert first.room_type == ChatRoom.RoomType.PRIVATE
        assert sorted(chat_service.room_member_pks(first)) == sorted([alice.pk, bob.pk])

    def test_private_chat_with_self_rejected(self, alice):
        with pytest.raises(DjangoValidationError):
            chat_service.get_or_create_private_room(alice, alice)

    def test_ai_room_is_per_user(self, alice, bob):
        assert chat_service.get_or_create_ai_room(alice).pk != chat_service.get_or_create_ai_room(bob).pk
        assert chat_service.get_or_create_ai_room(alice).pk == chat_service.get_or_create_ai_room(alice).pk


class TestAccessControl:
    def test_private_room_rejects_non_member(self, alice, bob):
        from system.models import UserInfo

        outsider = UserInfo.objects.create_user(username="eve", password="Test@123456")
        room = chat_service.get_or_create_private_room(alice, bob)
        assert chat_service.accessible_room(room.pk, alice).pk == room.pk
        with pytest.raises(DjangoValidationError):
            chat_service.accessible_room(room.pk, outsider)

    def test_ai_room_rejects_other_user(self, alice, bob):
        room = chat_service.get_or_create_ai_room(alice)
        with pytest.raises(DjangoValidationError):
            chat_service.accessible_room(room.pk, bob)

    def test_public_room_open_to_all(self, alice):
        room = chat_service.get_public_room()
        assert chat_service.accessible_room(room.pk, alice).pk == room.pk

    def test_unknown_room_rejected(self, alice):
        with pytest.raises(DjangoValidationError):
            chat_service.accessible_room(999999, alice)


class TestMessageCreate:
    def test_client_msg_id_is_idempotent(self, alice, bob):
        room = chat_service.get_or_create_private_room(alice, bob)
        first, created = chat_service.create_message(room, alice, "你好", client_msg_id="c-1")
        second, created_again = chat_service.create_message(room, alice, "你好", client_msg_id="c-1")
        assert created is True and created_again is False
        assert first.pk == second.pk
        assert ChatMessage.objects.filter(room=room).count() == 1

    def test_room_last_message_updated(self, alice, bob):
        room = chat_service.get_or_create_private_room(alice, bob)
        chat_service.create_message(room, alice, "最后一条")
        room.refresh_from_db()
        assert room.last_message == "最后一条"
        assert room.last_message_time is not None

    def test_content_validation(self, alice, bob):
        room = chat_service.get_or_create_private_room(alice, bob)
        with pytest.raises(DjangoValidationError):
            chat_service.create_message(room, alice, "   ")
        with pytest.raises(DjangoValidationError):
            chat_service.create_message(room, alice, "x" * 2001)

    def test_ai_message_allows_long_content(self, alice):
        room = chat_service.get_or_create_ai_room(alice)
        message, __ = chat_service.create_message(room, None, "y" * 5000, message_type=ChatMessage.MessageType.AI)
        assert len(message.content) == 5000
        assert message.sender_id is None

    def test_message_payload_shape(self, alice, bob):
        room = chat_service.get_or_create_private_room(alice, bob)
        message, __ = chat_service.create_message(room, alice, "payload")
        payload = chat_service.message_payload(message, room=room, sender=alice)
        assert payload["id"] == message.pk
        assert payload["room_id"] == room.pk
        assert payload["room_type"] == ChatRoom.RoomType.PRIVATE
        assert payload["sender_pk"] == alice.pk
        assert payload["sender_name"] == "爱丽丝"
        assert payload["content"] == "payload"


class TestUnreadCursor:
    def test_bump_unread_and_mark_read(self, alice, bob):
        from message.models import ChatRoomMember

        room = chat_service.get_or_create_private_room(alice, bob)
        message, __ = chat_service.create_message(room, alice, "未读测试")
        assert chat_service.bump_unread(room, alice.pk) == {bob.pk: 1}
        assert chat_service.mark_read(room, bob) == message.pk
        assert ChatRoomMember.objects.get(room=room, user=bob).unread_count == 0
        # 已读后再来一条：重新 +1（发送者自己不计未读）
        chat_service.create_message(room, alice, "第二条")
        assert chat_service.bump_unread(room, alice.pk) == {bob.pk: 1}

    def test_public_room_has_no_unread(self, alice):
        room = chat_service.get_public_room()
        message, __ = chat_service.create_message(room, alice, "公共消息")
        assert chat_service.bump_unread(room, alice.pk) == {}
        assert chat_service.mark_read(room, alice) == 0

    def test_mark_read_keeps_max_cursor(self, alice, bob):
        room = chat_service.get_or_create_private_room(alice, bob)
        first, __ = chat_service.create_message(room, alice, "1")
        second, __ = chat_service.create_message(room, alice, "2")
        chat_service.mark_read(room, bob, message_id=second.pk)
        assert chat_service.mark_read(room, bob, message_id=first.pk) == second.pk


class TestRecall:
    def test_sender_can_recall(self, alice, bob):
        room = chat_service.get_or_create_private_room(alice, bob)
        message, __ = chat_service.create_message(room, alice, "撤回我")
        recalled = chat_service.recall_message(alice, message.pk)
        assert recalled.is_recalled is True
        assert recalled.content == ""
        assert recalled.recalled_time is not None

    def test_others_cannot_recall(self, alice, bob):
        room = chat_service.get_or_create_private_room(alice, bob)
        message, __ = chat_service.create_message(room, alice, "不许撤回")
        with pytest.raises(DjangoValidationError):
            chat_service.recall_message(bob, message.pk)

    def test_expired_window_rejected(self, alice, bob):
        room = chat_service.get_or_create_private_room(alice, bob)
        message, __ = chat_service.create_message(room, alice, "过期")
        ChatMessage.objects.filter(pk=message.pk).update(created_time=timezone.now() - timezone.timedelta(minutes=3))
        with pytest.raises(DjangoValidationError):
            chat_service.recall_message(alice, message.pk)

    def test_recall_twice_rejected(self, alice, bob):
        room = chat_service.get_or_create_private_room(alice, bob)
        message, __ = chat_service.create_message(room, alice, "撤回两次")
        chat_service.recall_message(alice, message.pk)
        with pytest.raises(DjangoValidationError):
            chat_service.recall_message(alice, message.pk)


class TestMentions:
    def test_parse_mentions_all_positions_multi_target(self):
        assert chat_service.parse_mentions("你好 @alice 和 @bob 你们好") == ["alice", "bob"]
        assert chat_service.parse_mentions("@alice 你好") == ["alice"]
        assert chat_service.parse_mentions("没有提及") == []

    def test_parse_mentions_dedup(self):
        assert chat_service.parse_mentions("@alice @alice") == ["alice"]

    def test_mention_users_skips_self_and_unknown(self, alice, bob):
        users = chat_service.mention_users("@alice @bob @nobody 集合", exclude_username="alice")
        assert [user.pk for user in users] == [bob.pk]


class TestRoomListing:
    def test_list_user_rooms_public_first(self, alice):
        rooms = chat_service.list_user_rooms(alice, ai_enabled=False)
        assert rooms[0]["room_type"] == ChatRoom.RoomType.PUBLIC

    def test_list_user_rooms_includes_ai_and_private(self, alice, bob):
        ai_room = chat_service.get_or_create_ai_room(alice)
        chat_service.create_message(ai_room, None, "AI 你好", message_type=ChatMessage.MessageType.AI)
        room = chat_service.get_or_create_private_room(alice, bob)
        chat_service.create_message(room, bob, "私聊消息")
        chat_service.bump_unread(room, bob.pk)

        rooms = chat_service.list_user_rooms(alice, ai_enabled=True)
        types = [item["room_type"] for item in rooms]
        assert types[0] == ChatRoom.RoomType.PUBLIC
        assert ChatRoom.RoomType.AI in types
        private = next(item for item in rooms if item["room_type"] == ChatRoom.RoomType.PRIVATE)
        assert private["unread_count"] == 1
        assert private["peer"]["pk"] == bob.pk
        assert private["peer"]["nickname"] == "鲍勃"
