# -*- coding: utf-8 -*-
"""聊天附件（图片 / 音视频 / 文件消息）集成测试。

覆盖五条口径：
1. 上传端点：复用文件中心安全策略（种类门槛 / 扩展名 / 大小 / 配额）+ 落临时件；
   kind 白名单 image|video|audio|file 且必须与真实种类一致（伪造 kind 拒绝）；
2. WS 附件消息：归属 fail-closed（只能引用本人上传件）、消息类型与附件种类匹配、
   内容缺省取文件名、附件随消息转正（is_tmp=False）；
3. 受鉴权取件：房间可访问者可读、非成员 / 撤回后拒绝、文本消息无附件；
   图片走缩略图缓存（inline JPEG）；音/视频按真实 MIME inline（浏览器原生播放）；
   其余类型保持附件下载（Content-Disposition: attachment）；
4. 会话列表最后消息随附件消息更新；
5. 种类判定：mp4→video / mp3→audio / txt→file / png→image（MIME 优先、扩展名兜底，
   复用上传分类判定，不重复维护扩展名表）。

权限点（upload:ChatMessage / file:ChatMessage）在用例内以 menu_factory 现场登记并授权，
与种子口径一致（种子守护见 tests/unit/system/test_chat_menu_seed.py）。
"""

import base64

import pytest
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from file.models import UploadFile
from identity.models import UserRole
from message import chat as chat_service
from message.attachments import attachment_kind
from message.consumers import ChatNotify
from message.models import ChatMessage
from message.utils import get_chat_user_group_name, get_public_chat_group_name

pytestmark = pytest.mark.django_db

UPLOAD_URL = "/api/chat/message/upload"
# 2x2 纯色 PNG（最小合法图片，走 PIL 缩略图链路）
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR4nGP8zwACTGCSAQANHQEDgslx/wAAAABJRU5ErkJggg=="
)


@pytest.fixture
def alice(db):
    from identity.models import UserInfo

    return UserInfo.objects.create_user(username="alice", password="Test@123456", nickname="爱丽丝")


@pytest.fixture
def bob(db):
    from identity.models import UserInfo

    return UserInfo.objects.create_user(username="bob", password="Test@123456", nickname="鲍勃")


@pytest.fixture
def charlie(db):
    from identity.models import UserInfo

    return UserInfo.objects.create_user(username="charlie", password="Test@123456", nickname="卡罗")


@pytest.fixture
def chat_perms(menu_factory):
    """现场登记聊天附件权限点（与种子同 path/method）并按需授权给用户。"""
    upload_point = menu_factory("upload:ChatMessage", path="api/chat/message/upload$", method="POST")
    file_point = menu_factory("file:ChatMessage", path=r"api/chat/message/(?P<pk>[^/.]+)/file$", method="GET")

    def _grant(user):
        role = UserRole.objects.create(name=f"chat-{user.username}", code=f"chat-{user.pk}")
        role.menu.add(upload_point, file_point)
        user.roles.add(role)
        return user

    return {"grant": _grant}


@pytest.fixture
def ws_layer():
    from tests.channel_layer import reset_layer_state

    layer = get_channel_layer()
    reset_layer_state(layer)
    yield layer
    reset_layer_state(layer)


def _make_consumer(ws_layer, user, channel="specific.chat-attach"):
    consumer = ChatNotify()
    consumer.channel_layer = ws_layer
    consumer.channel_name = channel
    consumer.scope = {"user": user}
    consumer.user = user
    consumer.group_name = get_chat_user_group_name(user.pk)
    consumer.public_group = get_public_chat_group_name()
    consumer.disconnected = False

    captured = []

    async def fake_send_base_json(action, data=None, mid=None, code=1000, detail=None, close=False, **kwargs):
        captured.append({"action": action, "data": data, "code": code, "detail": detail})

    async def fake_close(code=None):
        return None

    consumer.send_base_json = fake_send_base_json
    consumer.close = fake_close
    return consumer, captured


def _capture_group_send(ws_layer, monkeypatch):
    sent = []
    original = ws_layer.group_send

    async def recording_group_send(group, message):
        sent.append({"group": group, "message": message})
        return await original(group, message)

    monkeypatch.setattr(ws_layer, "group_send", recording_group_send)
    return sent


def _api_client(user):
    client = APIClient(HTTP_USER_AGENT="pytest-agent")
    client.force_authenticate(user=user)
    return client


def _upload(client, name="pic.png", content=PNG_BYTES, content_type="image/png", kind="image"):
    payload = {"file": SimpleUploadedFile(name, content, content_type=content_type)}
    if kind is not None:
        payload["kind"] = kind
    return client.post(UPLOAD_URL, payload, format="multipart")


def _send(ws_layer, user, data):
    """驱动一次上行 chat_message（不依赖真实连接），返回 (consumer, captured)。"""
    consumer, captured = _make_consumer(ws_layer, user)

    async def scenario():
        await consumer.handle_send(data)

    async_to_sync(scenario)()
    return consumer, captured


class TestAttachmentUpload:
    def test_image_upload_returns_metadata(self, alice, chat_perms):
        chat_perms["grant"](alice)
        resp = _upload(_api_client(alice), kind="image")
        assert resp.data["code"] == 1000, resp.data
        data = resp.data["data"]
        assert data["kind"] == "image"
        assert data["filename"] == "pic.png"
        assert data["filesize"] == len(PNG_BYTES)
        upload = UploadFile.objects.get(pk=data["pk"])
        assert upload.is_tmp is True, "上传即临时件，发送消息后由服务端转正"
        assert upload.creator_id == alice.pk

    def test_kind_mismatch_rejected_without_leaving_record(self, alice, chat_perms):
        chat_perms["grant"](alice)
        resp = _upload(
            _api_client(alice),
            name="note.txt",
            content=b"hello",
            content_type="text/plain",
            kind="image",
        )
        assert resp.data["code"] == 1001, resp.data
        assert UploadFile.objects.filter(creator=alice).count() == 0, "种类不符的临时件应被清理"

    def test_unknown_kind_rejected(self, alice, chat_perms):
        """kind 白名单（image|video|audio|file）外的取值 fail-closed。"""
        chat_perms["grant"](alice)
        resp = _upload(_api_client(alice), name="pic.png", content=PNG_BYTES, content_type="image/png", kind="movie")
        assert resp.data["code"] == 1001, resp.data
        assert UploadFile.objects.filter(creator=alice).count() == 0

    def test_missing_kind_rejected(self, alice, chat_perms):
        """旧实现 kind 缺省不校验（任意记录可落），白名单收紧后缺省即拒绝。"""
        chat_perms["grant"](alice)
        resp = _upload(_api_client(alice), kind=None)
        assert resp.data["code"] == 1001, resp.data
        assert UploadFile.objects.filter(creator=alice).count() == 0

    def test_video_upload_accepted(self, alice, chat_perms):
        chat_perms["grant"](alice)
        resp = _upload(
            _api_client(alice),
            name="clip.mp4",
            content=b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom",
            content_type="video/mp4",
            kind="video",
        )
        assert resp.data["code"] == 1000, resp.data
        data = resp.data["data"]
        assert data["kind"] == "video"
        assert data["mime_type"] == "video/mp4"
        upload = UploadFile.objects.get(pk=data["pk"])
        assert upload.is_tmp is True

    def test_audio_upload_accepted(self, alice, chat_perms):
        chat_perms["grant"](alice)
        resp = _upload(
            _api_client(alice),
            name="song.mp3",
            content=b"ID3\x03\x00\x00\x00\x00\x00\x00",
            content_type="audio/mpeg",
            kind="audio",
        )
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["kind"] == "audio"

    def test_forged_video_kind_rejected(self, alice, chat_perms):
        """伪造 kind=video 上传文本文件：拒绝且不留无主记录。"""
        chat_perms["grant"](alice)
        resp = _upload(_api_client(alice), name="note.txt", content=b"hello", content_type="text/plain", kind="video")
        assert resp.data["code"] == 1001, resp.data
        assert UploadFile.objects.filter(creator=alice).count() == 0

    def test_forged_audio_kind_rejected(self, alice, chat_perms):
        chat_perms["grant"](alice)
        resp = _upload(
            _api_client(alice),
            name="clip.mp4",
            content=b"\x00\x00\x00\x18ftypmp42",
            content_type="video/mp4",
            kind="audio",
        )
        assert resp.data["code"] == 1001, resp.data
        assert UploadFile.objects.filter(creator=alice).count() == 0

    def test_file_kind_accepts_media(self, alice, chat_perms):
        """kind=file 对实际种类不限（音视频也可按文件消息发送，下载语义与旧行为一致）。"""
        chat_perms["grant"](alice)
        resp = _upload(
            _api_client(alice),
            name="clip.mp4",
            content=b"\x00\x00\x00\x18ftypmp42",
            content_type="video/mp4",
            kind="file",
        )
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["kind"] == "video", "记录真实种类，发送端决定按哪种消息类型引用"

    def test_blocked_extension_rejected(self, alice, chat_perms):
        chat_perms["grant"](alice)
        resp = _upload(
            _api_client(alice),
            name="evil.sh",
            content=b"echo hi",
            content_type="text/x-sh",
            kind="file",
        )
        assert resp.data["code"] == 1002, resp.data

    def test_permission_point_required(self, alice):
        """未授予 upload:ChatMessage 的普通用户被权限链拒绝（fail-closed）。"""
        resp = _upload(_api_client(alice), kind="image")
        assert resp.status_code == 403


class TestAttachmentMessage:
    def test_image_message_links_attachment_and_promotes_file(self, ws_layer, alice, chat_perms, monkeypatch):
        room = chat_service.get_public_room()
        _capture_group_send(ws_layer, monkeypatch)
        chat_perms["grant"](alice)
        file_pk = _upload(_api_client(alice), kind="image").data["data"]["pk"]

        __, captured = _send(
            ws_layer,
            alice,
            {
                "room_id": room.pk,
                "message_type": "image",
                "file_pk": file_pk,
                "client_msg_id": "c-img",
            },
        )

        assert captured == [], "成功路径不向发送方回错误帧"
        message = ChatMessage.objects.get(room=room)
        assert message.message_type == ChatMessage.MessageType.IMAGE
        assert message.content == "pic.png", "内容缺省取文件名（会话列表摘要可见）"
        assert str(message.attachment_id) == file_pk
        file_info = message.extra["file"]
        assert file_info["kind"] == "image"
        assert UploadFile.objects.get(pk=file_pk).is_tmp is False, "随消息落库即转正"
        # 取件 URL 由载荷层按消息 pk 派生（库里只存元信息快照）
        payload = chat_service.message_payload(message, room=room)
        assert payload["extra"]["file"]["url"] == f"/api/chat/message/{message.pk}/file"

    def test_broadcast_payload_carries_attachment_url(self, ws_layer, alice, chat_perms, monkeypatch):
        room = chat_service.get_public_room()
        sent = _capture_group_send(ws_layer, monkeypatch)
        chat_perms["grant"](alice)
        file_pk = _upload(_api_client(alice), kind="image").data["data"]["pk"]

        _send(ws_layer, alice, {"room_id": room.pk, "message_type": "image", "file_pk": file_pk})

        payload = sent[0]["message"]["data"]
        assert payload["message_type"] == "image"
        assert payload["extra"]["file"]["url"].startswith("/api/chat/message/")
        assert payload["extra"]["file"]["missing"] is False

    def test_attachment_must_belong_to_sender(self, ws_layer, alice, bob, chat_perms):
        room = chat_service.get_public_room()
        chat_perms["grant"](alice)
        file_pk = _upload(_api_client(alice), kind="image").data["data"]["pk"]

        __, captured = _send(ws_layer, bob, {"room_id": room.pk, "message_type": "image", "file_pk": file_pk})

        assert captured[0]["code"] == 1001, "引用他人文件必须 fail-closed"
        assert ChatMessage.objects.filter(room=room).count() == 0

    def test_image_message_rejects_non_image_attachment(self, ws_layer, alice, chat_perms):
        room = chat_service.get_public_room()
        chat_perms["grant"](alice)
        file_pk = _upload(
            _api_client(alice), name="note.txt", content=b"hello", content_type="text/plain", kind="file"
        ).data["data"]["pk"]

        __, captured = _send(ws_layer, alice, {"room_id": room.pk, "message_type": "image", "file_pk": file_pk})

        assert captured[0]["code"] == 1001
        assert ChatMessage.objects.filter(room=room).count() == 0

    def test_text_message_has_no_attachment(self, ws_layer, alice):
        room = chat_service.get_public_room()
        _send(ws_layer, alice, {"room_id": room.pk, "content": "普通文本"})
        message = ChatMessage.objects.get(room=room)
        assert message.attachment_id is None
        assert message.message_type == ChatMessage.MessageType.TEXT


class TestAttachmentServing:
    @pytest.fixture
    def image_message(self, alice, bob):
        """alice 与 bob 私聊房间内的一张图片消息（charlie 非成员）。"""
        room = chat_service.get_or_create_private_room(alice, bob)
        upload = UploadFile.objects.create(
            creator=alice,
            filename="pic.png",
            filesize=len(PNG_BYTES),
            mime_type="image/png",
            is_upload=True,
            category="image",
            filepath=SimpleUploadedFile("pic.png", PNG_BYTES, content_type="image/png"),
        )
        message, __ = chat_service.create_message(
            room,
            alice,
            "",
            message_type=ChatMessage.MessageType.IMAGE,
            attachment=upload,
        )
        return room, message

    def test_room_member_can_read_thumbnail(self, image_message, bob, chat_perms):
        chat_perms["grant"](bob)
        __, message = image_message
        resp = _api_client(bob).get(f"/api/chat/message/{message.pk}/file?size=thumb")
        assert resp.status_code == 200, getattr(resp, "data", None)
        assert resp["Content-Type"].startswith("image/jpeg")

    def test_non_member_rejected(self, image_message, charlie, chat_perms):
        chat_perms["grant"](charlie)
        __, message = image_message
        resp = _api_client(charlie).get(f"/api/chat/message/{message.pk}/file")
        assert resp.data["code"] == 1001, "非房间成员不可取件"

    def test_recalled_message_file_not_available(self, image_message, alice, chat_perms):
        chat_perms["grant"](alice)
        __, message = image_message
        chat_service.recall_message(alice, message.pk)
        resp = _api_client(alice).get(f"/api/chat/message/{message.pk}/file")
        assert resp.data["code"] == 1001, "撤回后附件不可再取"

    def test_text_message_has_no_file(self, alice, bob, chat_perms):
        chat_perms["grant"](alice)
        room = chat_service.get_or_create_private_room(alice, bob)
        message, __ = chat_service.create_message(room, alice, "只有文本")
        resp = _api_client(alice).get(f"/api/chat/message/{message.pk}/file")
        assert resp.data["code"] == 1001

    def test_file_message_downloads_with_attachment_disposition(self, alice, bob, chat_perms):
        chat_perms["grant"](bob)
        room = chat_service.get_or_create_private_room(alice, bob)
        upload = UploadFile.objects.create(
            creator=alice,
            filename="note.txt",
            filesize=5,
            mime_type="text/plain",
            is_upload=True,
            category="document",
            filepath=SimpleUploadedFile("note.txt", b"hello", content_type="text/plain"),
        )
        message, __ = chat_service.create_message(
            room,
            alice,
            "",
            message_type=ChatMessage.MessageType.FILE,
            attachment=upload,
        )
        resp = _api_client(bob).get(f"/api/chat/message/{message.pk}/file")
        assert resp.status_code == 200
        assert resp["Content-Disposition"].startswith("attachment")

    def test_payload_marks_missing_after_file_removed(self, alice, bob, image_message):
        room, message = image_message
        message.attachment.hard_delete()  # 模拟附件被清理（外键 SET_NULL）
        message.refresh_from_db()
        payload = chat_service.message_payload(message, room=room)
        assert payload["extra"]["file"]["missing"] is True
        assert payload["extra"]["file"]["url"] == ""


class TestAttachmentKindInference:
    """种类判定：MIME 前缀优先、扩展名兜底（复用上传分类判定）。"""

    @pytest.mark.parametrize(
        "filename,mime_type,expected",
        [
            ("clip.mp4", "video/mp4", "video"),
            ("clip.mov", "application/octet-stream", "video"),  # MIME 缺失按扩展名兜底
            ("song.mp3", "audio/mpeg", "audio"),
            ("song.flac", "application/octet-stream", "audio"),
            ("note.txt", "text/plain", "file"),
            ("doc.pdf", "application/pdf", "file"),
            ("archive.zip", "application/zip", "file"),
        ],
    )
    def test_media_and_file_kinds(self, filename, mime_type, expected):
        assert attachment_kind(UploadFile(filename=filename, mime_type=mime_type)) == expected

    def test_image_kind_unchanged(self):
        """图片分支零漂移：仍按在线预览判定（MIME 前缀），不引入扩展名兜底。"""
        assert attachment_kind(UploadFile(filename="pic.png", mime_type="image/png")) == "image"
        # 无 MIME 的 .png 历史口径为 file（下载），不因音视频扩展名兜底而漂移
        assert attachment_kind(UploadFile(filename="pic.png", mime_type="")) == "file"


class TestMediaMessage:
    def test_video_message_links_attachment(self, ws_layer, alice, chat_perms, monkeypatch):
        room = chat_service.get_public_room()
        _capture_group_send(ws_layer, monkeypatch)
        chat_perms["grant"](alice)
        file_pk = _upload(
            _api_client(alice),
            name="clip.mp4",
            content=b"\x00\x00\x00\x18ftypmp42",
            content_type="video/mp4",
            kind="video",
        ).data["data"]["pk"]

        __, captured = _send(
            ws_layer,
            alice,
            {"room_id": room.pk, "message_type": "video", "file_pk": file_pk, "client_msg_id": "c-video"},
        )

        assert captured == []
        message = ChatMessage.objects.get(room=room)
        assert message.message_type == ChatMessage.MessageType.VIDEO
        assert message.content == "clip.mp4", "内容缺省取文件名"
        assert message.extra["file"]["kind"] == "video"
        assert UploadFile.objects.get(pk=file_pk).is_tmp is False

    def test_video_message_rejects_non_video_attachment(self, ws_layer, alice, chat_perms):
        """消息类型与附件种类匹配：video 消息只接受视频附件（fail-closed）。"""
        room = chat_service.get_public_room()
        chat_perms["grant"](alice)
        file_pk = _upload(
            _api_client(alice), name="note.txt", content=b"hello", content_type="text/plain", kind="file"
        ).data["data"]["pk"]

        __, captured = _send(ws_layer, alice, {"room_id": room.pk, "message_type": "video", "file_pk": file_pk})

        assert captured[0]["code"] == 1001
        assert ChatMessage.objects.filter(room=room).count() == 0


class TestMediaServing:
    """音/视频取件：inline + 真实 Content-Type（浏览器原生播放）；图片分支不回归。"""

    @pytest.fixture
    def media_room(self, alice, bob):
        return chat_service.get_or_create_private_room(alice, bob)

    def _media_message(self, room, alice, filename, mime_type, message_type):
        upload = UploadFile.objects.create(
            creator=alice,
            filename=filename,
            filesize=64,
            mime_type=mime_type,
            is_upload=True,
            category=message_type,
            filepath=SimpleUploadedFile(filename, b"\x00" * 64, content_type=mime_type),
        )
        message, __ = chat_service.create_message(room, alice, "", message_type=message_type, attachment=upload)
        return message

    def test_video_served_inline_with_real_mime(self, media_room, alice, bob, chat_perms):
        chat_perms["grant"](bob)
        message = self._media_message(media_room, alice, "clip.mp4", "video/mp4", ChatMessage.MessageType.VIDEO)
        resp = _api_client(bob).get(f"/api/chat/message/{message.pk}/file")
        assert resp.status_code == 200, getattr(resp, "data", None)
        assert resp["Content-Type"].startswith("video/mp4")
        assert resp["Content-Disposition"].startswith("inline")
        assert resp["X-Content-Type-Options"] == "nosniff"

    def test_audio_served_inline_with_real_mime(self, media_room, alice, bob, chat_perms):
        chat_perms["grant"](bob)
        message = self._media_message(media_room, alice, "song.mp3", "audio/mpeg", ChatMessage.MessageType.AUDIO)
        resp = _api_client(bob).get(f"/api/chat/message/{message.pk}/file")
        assert resp.status_code == 200, getattr(resp, "data", None)
        assert resp["Content-Type"].startswith("audio/mpeg")
        assert resp["Content-Disposition"].startswith("inline")

    def test_video_non_member_rejected(self, media_room, alice, charlie, chat_perms):
        """音/视频 inline 不降低鉴权口径：非房间成员拒绝。"""
        chat_perms["grant"](charlie)
        message = self._media_message(media_room, alice, "clip.mp4", "video/mp4", ChatMessage.MessageType.VIDEO)
        resp = _api_client(charlie).get(f"/api/chat/message/{message.pk}/file")
        assert resp.data["code"] == 1001


class TestLastMessageSummary:
    def test_attachment_message_updates_room_summary(self, ws_layer, alice, chat_perms):
        room = chat_service.get_public_room()
        chat_perms["grant"](alice)
        file_pk = _upload(_api_client(alice), kind="image").data["data"]["pk"]
        _send(ws_layer, alice, {"room_id": room.pk, "message_type": "image", "file_pk": file_pk})
        room.refresh_from_db()
        assert room.last_message == "pic.png"
        assert room.last_message_time is not None
