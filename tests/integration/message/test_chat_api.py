# -*- coding: utf-8 -*-
"""聊天室 REST 集成测试。

覆盖：会话列表（公共置顶 / AI 按门禁显隐 / 私聊未读）、私聊开通幂等与自聊拒绝、
历史游标分页与可撤回标记、撤回（越权/超窗拒绝 + 房间广播）、联系人、
AI 双形态（多轮 / `/kb` 引用来源 / 门禁 / 降级）、菜单权限门控。
"""

import pytest
from rest_framework.test import APIClient

from message import chat as chat_service
from message.models import ChatMessage, ChatRoom

pytestmark = pytest.mark.django_db

ROOM_URL = "/api/chat/room"
MESSAGE_URL = "/api/chat/message"
CONTACT_URL = "/api/chat/contacts"
AI_URL = "/api/chat/ai/message"


@pytest.fixture
def alice(db):
    from system.models import UserInfo

    return UserInfo.objects.create_user(username="alice", password="Test@123456", nickname="爱丽丝")


@pytest.fixture
def bob(db):
    from system.models import UserInfo

    return UserInfo.objects.create_user(username="bob", password="Test@123456", nickname="鲍勃")


@pytest.fixture
def ai_enabled(settings):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_BASE_URL = "https://ai.example.com/v1"
    settings.AI_API_KEY = "sk-test"
    settings.AI_MODEL = "test-model"
    return settings


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class StubLLM:
    def __init__(self, answer="这是 AI 的回答。"):
        self.answer = answer
        self.requests = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.requests.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse({"choices": [{"message": {"content": self.answer}}]})


@pytest.fixture
def stub_llm(monkeypatch):
    stub = StubLLM()
    monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient._client", lambda self: stub)
    return stub


class TestRoomList:
    def test_public_room_is_first(self, auth_client):
        body = auth_client.get(ROOM_URL).json()["data"]
        assert body["rooms"][0]["room_type"] == ChatRoom.RoomType.PUBLIC
        assert body["ai_enabled"] is False
        assert body["ai_command"] == "/kb"

    def test_ai_room_visible_only_when_enabled(self, auth_client, ai_enabled):
        body = auth_client.get(ROOM_URL).json()["data"]
        assert body["ai_enabled"] is True
        assert any(item["room_type"] == ChatRoom.RoomType.AI for item in body["rooms"])

    def test_private_room_with_unread_listed(self, auth_client, superuser, bob):
        room = chat_service.get_or_create_private_room(superuser, bob)
        chat_service.create_message(room, bob, "在吗")
        chat_service.bump_unread(room, bob.pk)
        body = auth_client.get(ROOM_URL).json()["data"]
        private = next(item for item in body["rooms"] if item["room_type"] == ChatRoom.RoomType.PRIVATE)
        assert private["unread_count"] == 1
        assert private["peer"]["username"] == "bob"
        assert private["last_message"] == "在吗"


class TestOpenPrivate:
    def test_open_private_idempotent(self, auth_client, superuser, bob):
        first = auth_client.post(f"{ROOM_URL}/open-private", {"user_pk": bob.pk}, format="json")
        second = auth_client.post(f"{ROOM_URL}/open-private", {"user_pk": bob.pk}, format="json")
        assert first.status_code == 200, first.data
        assert first.json()["data"]["id"] == second.json()["data"]["id"]
        assert ChatRoom.objects.filter(room_type=ChatRoom.RoomType.PRIVATE).count() == 1

    def test_open_private_with_self_rejected(self, auth_client, superuser):
        response = auth_client.post(f"{ROOM_URL}/open-private", {"user_pk": superuser.pk}, format="json")
        assert response.json()["code"] == 1001

    def test_open_private_unknown_user(self, auth_client):
        response = auth_client.post(f"{ROOM_URL}/open-private", {"user_pk": 999999}, format="json")
        assert response.json()["code"] == 1001


class TestHistory:
    def test_history_ascending_with_pagination(self, auth_client, superuser, bob):
        room = chat_service.get_or_create_private_room(superuser, bob)
        for index in range(4):
            chat_service.create_message(room, superuser, f"消息{index}")

        first_page = auth_client.get(f"{MESSAGE_URL}?room={room.pk}&limit=2").json()["data"]
        assert first_page["has_more"] is True
        assert [item["content"] for item in first_page["results"]] == ["消息2", "消息3"]
        assert first_page["room"]["id"] == room.pk

        before_id = first_page["results"][0]["id"]
        second_page = auth_client.get(f"{MESSAGE_URL}?room={room.pk}&limit=2&before_id={before_id}").json()["data"]
        assert [item["content"] for item in second_page["results"]] == ["消息0", "消息1"]
        assert second_page["has_more"] is False

    def test_history_marks_own_recent_message_recallable(self, auth_client, superuser, bob):
        room = chat_service.get_or_create_private_room(superuser, bob)
        mine, __ = chat_service.create_message(room, superuser, "我的消息")
        theirs, __ = chat_service.create_message(room, bob, "对方消息")
        rows = auth_client.get(f"{MESSAGE_URL}?room={room.pk}").json()["data"]["results"]
        flags = {item["id"]: item["can_recall"] for item in rows}
        assert flags[mine.pk] is True
        assert flags[theirs.pk] is False

    def test_history_rejects_non_member(self, alice, bob, superuser):
        """私聊可访问性 fail-closed：非成员（含超管）读不到他人会话历史。"""
        room = chat_service.get_or_create_private_room(alice, bob)
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=superuser)
        assert client.get(f"{MESSAGE_URL}?room={room.pk}").json()["code"] == 1001

    def test_history_public_room_accessible(self, auth_client):
        room = chat_service.get_public_room()
        chat_service.create_message(room, None, "系统公告", message_type=ChatMessage.MessageType.SYSTEM)
        body = auth_client.get(f"{MESSAGE_URL}?room={room.pk}").json()["data"]
        assert body["results"][0]["content"] == "系统公告"

    def test_invalid_cursor_rejected(self, auth_client):
        room = chat_service.get_public_room()
        response = auth_client.get(f"{MESSAGE_URL}?room={room.pk}&before_id=abc")
        assert response.json()["code"] == 1001


class TestRecall:
    def test_recall_success_and_broadcast(self, auth_client, superuser, bob, monkeypatch):
        room = chat_service.get_or_create_private_room(superuser, bob)
        message, __ = chat_service.create_message(room, superuser, "撤回我")
        calls = []
        monkeypatch.setattr(
            "message.views.push_room_event", lambda room, payload, message_type=None: calls.append(payload)
        )

        response = auth_client.post(f"{MESSAGE_URL}/{message.pk}/recall", {}, format="json")
        assert response.status_code == 200, response.data
        assert calls and calls[0]["message_id"] == message.pk
        message.refresh_from_db()
        assert message.is_recalled is True and message.content == ""

    def test_recall_other_user_message_rejected(self, auth_client, superuser, bob):
        room = chat_service.get_or_create_private_room(superuser, bob)
        message, __ = chat_service.create_message(room, bob, "别人的消息")
        response = auth_client.post(f"{MESSAGE_URL}/{message.pk}/recall", {}, format="json")
        assert response.json()["code"] == 1001

    def test_recall_missing_message_rejected(self, auth_client):
        response = auth_client.post(f"{MESSAGE_URL}/999999/recall", {}, format="json")
        assert response.json()["code"] == 1001


class TestContacts:
    def test_contacts_returns_recent_users(self, auth_client, superuser, bob):
        from system.models import UserSession

        UserSession.objects.create(creator=bob, channel_name="chan-bob")
        body = auth_client.get(CONTACT_URL).json()["data"]
        assert [item["username"] for item in body["results"]] == ["bob"]
        assert body["results"][0]["online"] is False

    def test_contacts_excludes_self(self, auth_client, superuser):
        from system.models import UserSession

        UserSession.objects.create(creator=superuser, channel_name="chan-self")
        body = auth_client.get(CONTACT_URL).json()["data"]
        assert body["results"] == []


class TestAiChat:
    def test_disabled_gate(self, auth_client, settings):
        settings.AI_ASSISTANT_ENABLED = False
        response = auth_client.post(AI_URL, {"content": "你好"}, format="json")
        assert response.json()["code"] == 1001
        assert not ChatMessage.objects.exists()

    def test_multiturn_creates_user_and_ai_messages(self, auth_client, superuser, ai_enabled, stub_llm):
        response = auth_client.post(AI_URL, {"content": "请介绍一下系统"}, format="json")
        assert response.status_code == 200, response.data
        body = response.json()["data"]
        assert body["mode"] == "chat"
        assert body["message"]["message_type"] == ChatMessage.MessageType.AI
        assert body["message"]["content"] == "这是 AI 的回答。"
        assert body["question"]["content"] == "请介绍一下系统"

        room = ChatRoom.objects.get(room_key=f"ai:{superuser.pk}")
        rows = list(ChatMessage.objects.filter(room=room).order_by("id"))
        assert [row.message_type for row in rows] == [ChatMessage.MessageType.TEXT, ChatMessage.MessageType.AI]

        # 第二轮带上历史上下文（system 人设 + 上轮 user/assistant + 本轮提问）
        auth_client.post(AI_URL, {"content": "继续"}, format="json")
        messages = stub_llm.requests[-1]["json"]["messages"]
        assert messages[0]["role"] == "system"
        assert [item["role"] for item in messages[1:]] == ["user", "assistant", "user"]

    def test_kb_command_returns_sources(self, auth_client, ai_enabled, monkeypatch):
        monkeypatch.setattr(
            "system.utils.ai.ask",
            lambda question: {
                "answer": "根据文档，重置密码见 [1]。",
                "sources": [{"title": "手册", "path": "upload/manual.md", "chunk_index": 0}],
            },
        )
        response = auth_client.post(AI_URL, {"content": "/kb 如何重置密码"}, format="json")
        body = response.json()["data"]
        assert body["mode"] == "kb"
        assert body["message"]["extra"]["sources"][0]["path"] == "upload/manual.md"

    def test_kb_without_question_rejected(self, auth_client, ai_enabled, stub_llm):
        response = auth_client.post(AI_URL, {"content": "/kb"}, format="json")
        assert response.json()["code"] == 1001

    def test_llm_failure_degrades_to_system_message(self, auth_client, superuser, ai_enabled, monkeypatch):
        from common.sdk.ai.chat import AiSdkError

        monkeypatch.setattr(
            "common.sdk.ai.chat.ChatCompletionsClient.chat",
            lambda self, messages, temperature=0.2: (_ for _ in ()).throw(AiSdkError("provider down")),
        )
        response = auth_client.post(AI_URL, {"content": "你好"}, format="json")
        assert response.json()["code"] == 1001
        fallback = ChatMessage.objects.filter(message_type=ChatMessage.MessageType.SYSTEM).first()
        assert fallback is not None
        assert fallback.extra.get("error") is True
        assert response.json()["data"]["message"]["id"] == fallback.pk

    def test_ai_room_of_other_user_rejected(self, auth_client, bob, ai_enabled, stub_llm):
        room = chat_service.get_or_create_ai_room(bob)
        response = auth_client.post(AI_URL, {"content": "偷看", "room_id": room.pk}, format="json")
        assert response.json()["code"] == 1001

    def test_non_ai_room_rejected(self, auth_client, superuser, bob, ai_enabled, stub_llm):
        room = chat_service.get_or_create_private_room(superuser, bob)
        response = auth_client.post(AI_URL, {"content": "hello", "room_id": room.pk}, format="json")
        assert response.json()["code"] == 1001


class TestPermissionGate:
    def _grant(self, user, *points):
        from system.models import Menu, MenuMeta

        menus = []
        for name in points:
            meta = MenuMeta.objects.create(title=name)
            menus.append(
                Menu.objects.create(
                    name=name,
                    path={
                        "list:ChatRoom": "api/chat/room$",
                        "list:ChatMessage": "api/chat/message$",
                    }[name],
                    method="GET",
                    menu_type=Menu.MenuChoices.PERMISSION,
                    meta=meta,
                )
            )
        user.roles.first().menu.set(menus)

    def test_normal_user_without_menu_rejected(self, normal_user):
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        assert client.get(ROOM_URL).status_code == 403

    def test_normal_user_with_menu_allowed(self, normal_user):
        self._grant(normal_user, "list:ChatRoom", "list:ChatMessage")
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        assert client.get(ROOM_URL).status_code == 200
        assert client.get(f"{MESSAGE_URL}?room={chat_service.get_public_room().pk}").status_code == 200
        # 未授予的撤回接口仍拒绝
        assert client.post(f"{MESSAGE_URL}/1/recall", {}, format="json").status_code == 403
