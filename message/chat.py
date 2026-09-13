#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天室领域服务（ADR-034）：会话开通 / 消息落库 / 未读游标 / 联系人 / @提及。

分层约定：
- 本模块只做同步 DB 与纯函数逻辑（REST 视图与 WS consumer 共用同一套语义）：
  WS consumer 经 `database_sync_to_async` 调用，REST 视图直接调用；
- 消息广播（channel layer）不在本模块，由 message/utils.py 与 ws/chat consumer 负责，
  避免同步/异步边界混杂；
- 可访问性一律 fail-closed：非成员访问私聊、非归属访问他人 AI 会话均按「房间不存在」拒绝。
"""

import re
import uuid

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Max
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from message.models import (
    AI_MAX_CONTENT_LENGTH,
    MAX_CONTENT_LENGTH,
    RECALL_WINDOW_MINUTES,
    ChatMessage,
    ChatRoom,
    ChatRoomMember,
    ai_room_key,
    private_room_key,
)

logger = get_logger(__name__)

# @提及解析：@username（用户名允许字符集见 Django UnicodeUsernameValidator）
MENTION_PATTERN = re.compile(r"@([\w.\-]+)")
# 会话列表 / 联系人默认条数
ROOM_LIST_LIMIT = 100
CONTACT_LIMIT = 100
# AI 多轮上下文条数
AI_CONTEXT_LIMIT = 20


def _user_pk(obj):
    return getattr(obj, "pk", obj)


def display_name(user) -> str:
    """消息/联系人展示名：优先昵称，回退用户名。"""
    if user is None:
        return ""
    return (getattr(user, "nickname", "") or getattr(user, "username", "") or "")[:64]


def avatar_url(user) -> str:
    """头像 URL（无头像/存储异常返回空串，前端回退首字母头像）。"""
    avatar = getattr(user, "avatar", None)
    if not avatar:
        return ""
    try:
        return avatar.url
    except Exception:  # noqa: BLE001 存储异常不应影响消息链路
        return ""


def user_brief(user) -> dict:
    """用户简介（会话对端 / 联系人共用）。"""
    if user is None:
        return {}
    return {
        "pk": user.pk,
        "username": getattr(user, "username", "") or "",
        "nickname": getattr(user, "nickname", "") or "",
        "avatar": avatar_url(user),
    }


# ---------------------------------------------------------------- 会话开通


def get_public_room() -> ChatRoom:
    """公共聊天室（全站单例；展示名走前端 i18n，此处只存稳定 key）。"""
    room, __ = ChatRoom.objects.get_or_create(
        room_key="public", defaults={"room_type": ChatRoom.RoomType.PUBLIC, "name": "Public chat room"}
    )
    return room


def get_or_create_private_room(user_a, user_b) -> ChatRoom:
    """一对一私聊房间：`dm:{min_pk}:{max_pk}` 幂等，双方各建一条未读游标行。"""
    pk_a, pk_b = _user_pk(user_a), _user_pk(user_b)
    if pk_a == pk_b:
        raise DjangoValidationError(_("Cannot start a private chat with yourself"))
    key = private_room_key(pk_a, pk_b)
    room = ChatRoom.objects.filter(room_key=key).first()
    if room is None:
        try:
            with transaction.atomic():
                room = ChatRoom.objects.create(room_key=key, room_type=ChatRoom.RoomType.PRIVATE)
        except IntegrityError:  # 并发开通：另一请求已创建
            room = ChatRoom.objects.get(room_key=key)
    from system.models import UserInfo

    for user in UserInfo.objects.filter(pk__in=[pk_a, pk_b]):
        ChatRoomMember.objects.get_or_create(room=room, user=user)
    return room


def get_or_create_ai_room(owner) -> ChatRoom:
    """AI 助手房间：每用户一间（`ai:{pk}`），归属校验靠 owner。"""
    key = ai_room_key(_user_pk(owner))
    room = ChatRoom.objects.filter(room_key=key).first()
    if room is None:
        try:
            with transaction.atomic():
                room = ChatRoom.objects.create(room_key=key, room_type=ChatRoom.RoomType.AI, owner_id=_user_pk(owner))
        except IntegrityError:
            room = ChatRoom.objects.get(room_key=key)
        ChatRoomMember.objects.get_or_create(room=room, user=owner)
    return room


def accessible_room(room_id, user) -> ChatRoom:
    """按可访问性取房间（fail-closed）：公共全员可进、私聊仅成员、AI 仅归属者。"""
    room = ChatRoom.objects.filter(pk=room_id, is_active=True).first()
    if room is None:
        raise DjangoValidationError(_("Chat room not found"))
    if room.room_type == ChatRoom.RoomType.PUBLIC:
        return room
    if room.room_type == ChatRoom.RoomType.AI:
        if room.owner_id != _user_pk(user):
            raise DjangoValidationError(_("Chat room not found"))
        return room
    if not ChatRoomMember.objects.filter(room=room, user_id=_user_pk(user)).exists():
        raise DjangoValidationError(_("Chat room not found"))
    return room


def room_member_pks(room) -> list:
    """房间成员 pk 列表（公共房间返回空：走公共广播组）。"""
    if room.room_type == ChatRoom.RoomType.PUBLIC:
        return []
    return list(room.members.values_list("user_id", flat=True))


# ---------------------------------------------------------------- 消息


def validate_content(content: str, limit: int = MAX_CONTENT_LENGTH) -> str:
    content = (content or "").strip()
    if not content:
        raise DjangoValidationError(_("Message content cannot be empty"))
    if len(content) > limit:
        raise DjangoValidationError(_("Message is too long (max {} characters)").format(limit))
    return content


def create_message(
    room: ChatRoom,
    sender,
    content: str,
    message_type: str = ChatMessage.MessageType.TEXT,
    client_msg_id: str = "",
    extra: dict = None,
) -> tuple:
    """落库一条消息，返回 (message, created)。

    幂等：同一发送者 + 同一 client_msg_id 命中已有消息时直接返回旧消息
    （断线重发 / 乐观上屏重复提交都不产生第二行）。
    AI / 系统消息放宽长度上限（模型回答常超用户输入上限）。
    """
    limit = MAX_CONTENT_LENGTH if message_type == ChatMessage.MessageType.TEXT else AI_MAX_CONTENT_LENGTH
    content = validate_content(content, limit)
    sender_pk = _user_pk(sender) if sender is not None else None
    if sender_pk and client_msg_id:
        existing = ChatMessage.objects.filter(sender_id=sender_pk, client_msg_id=client_msg_id).first()
        if existing is not None:
            return existing, False
    try:
        with transaction.atomic():
            message = ChatMessage.objects.create(
                room=room,
                sender_id=sender_pk,
                sender_name=display_name(sender) if sender is not None else "",
                message_type=message_type,
                content=content,
                client_msg_id=client_msg_id or "",
                extra=extra or {},
            )
            ChatRoom.objects.filter(pk=room.pk).update(
                last_message=content[:200], last_message_time=message.created_time or timezone.now()
            )
    except IntegrityError:  # 并发同 client_msg_id：复用已落库的那条
        message = ChatMessage.objects.filter(sender_id=sender_pk, client_msg_id=client_msg_id).first()
        if message is None:
            raise
        return message, False
    room.last_message = content[:200]
    room.last_message_time = message.created_time or timezone.now()
    return message, True


def message_payload(message: ChatMessage, room=None, sender=None, avatar_map: dict = None) -> dict:
    """消息 → 前端渲染载荷（WS 广播 / REST 历史共用同一形状）。

    room / sender / avatar_map 为可选预取参数：批量场景（历史列表）传入 avatar_map
    可避免逐条查询发送者头像。
    """
    if avatar_map is None:
        if sender is None and message.sender_id:
            from system.models import UserInfo

            sender = UserInfo.objects.filter(pk=message.sender_id).first()
        avatar = avatar_url(sender)
    else:
        avatar = avatar_map.get(message.sender_id, "")
    room_type = ""
    if room is not None:
        room_type = room.room_type
    return {
        "id": message.pk,
        "room_id": message.room_id,
        "room_type": room_type,
        "sender_pk": message.sender_id,
        "sender_name": message.sender_name,
        "sender_avatar": avatar,
        "message_type": message.message_type,
        "content": message.content,
        "is_recalled": message.is_recalled,
        "created_time": (message.created_time or timezone.now()).isoformat(),
        "client_msg_id": message.client_msg_id,
        "extra": message.extra or {},
    }


def sender_avatar_map(messages: list) -> dict:
    """一批消息的发送者头像映射（一次查询，供历史列表/广播批量使用）。"""
    from system.models import UserInfo

    pks = {message.sender_id for message in messages if message.sender_id}
    if not pks:
        return {}
    result = {}
    for user in UserInfo.objects.filter(pk__in=pks).only("pk", "avatar"):
        result[user.pk] = avatar_url(user)
    return result


serialize_message = message_payload  # 兼容别名（WS 广播语义）


def bump_unread(room: ChatRoom, exclude_pk=None) -> dict:
    """未读计数 +1（除发送者），返回 {user_pk: unread_count}（仅私聊/AI 会话）。"""
    if room.room_type == ChatRoom.RoomType.PUBLIC:
        return {}
    queryset = room.members.all()
    if exclude_pk is not None:
        queryset = queryset.exclude(user_id=exclude_pk)
    if not queryset.update(unread_count=F("unread_count") + 1):
        return {}
    return dict(room.members.exclude(user_id=exclude_pk).values_list("user_id", "unread_count"))


def mark_read(room: ChatRoom, user, message_id=None) -> int:
    """清零未读并推进已读游标，返回最新游标（公共房间返回 0）。"""
    if room.room_type == ChatRoom.RoomType.PUBLIC:
        return 0
    latest = message_id or ChatMessage.objects.filter(room=room).aggregate(Max("id"))["id__max"] or 0
    member = ChatRoomMember.objects.filter(room=room, user_id=_user_pk(user)).first()
    if member is None:
        return 0
    member.last_read_id = max(member.last_read_id or 0, int(latest))
    member.unread_count = 0
    member.save(update_fields=["last_read_id", "unread_count", "updated_time"])
    return member.last_read_id


def recall_message(user, message_id) -> ChatMessage:
    """撤回：仅本人、窗口内、未撤回（超窗/越权返回可读校验错误）。"""
    message = ChatMessage.objects.filter(pk=message_id).first()
    if message is None:
        raise DjangoValidationError(_("Message not found"))
    if message.sender_id != _user_pk(user):
        raise DjangoValidationError(_("Only the sender can recall the message"))
    if message.is_recalled:
        raise DjangoValidationError(_("Message already recalled"))
    created = message.created_time or timezone.now()
    if timezone.now() - created > timezone.timedelta(minutes=RECALL_WINDOW_MINUTES):
        raise DjangoValidationError(_("Messages can only be recalled within {} minutes").format(RECALL_WINDOW_MINUTES))
    message.is_recalled = True
    message.recalled_time = timezone.now()
    message.content = ""
    message.save(update_fields=["is_recalled", "recalled_time", "content", "updated_time"])
    return message


# ---------------------------------------------------------------- 列表


def room_to_dict(room: ChatRoom, user, unread_count: int = 0, online_pks: set = None) -> dict:
    """会话列表行（前端左侧栏渲染契约）。"""
    peer = None
    if room.room_type == ChatRoom.RoomType.PRIVATE:
        from system.models import UserInfo

        peer_obj = UserInfo.objects.filter(
            pk__in=room.members.exclude(user_id=_user_pk(user)).values("user_id")
        ).first()
        if peer_obj is not None:
            peer = user_brief(peer_obj)
            if online_pks is not None:
                peer["online"] = peer_obj.pk in online_pks
    return {
        "id": room.pk,
        "room_type": room.room_type,
        "room_key": room.room_key,
        "name": room.name,
        "peer": peer,
        "last_message": room.last_message,
        "last_message_time": room.last_message_time.isoformat() if room.last_message_time else "",
        "unread_count": unread_count,
    }


def online_user_pks() -> set:
    """在线用户快照（失败降级为空集：在线态属增强展示，不应阻断会话列表）。"""
    try:
        from message.utils import get_online_users

        return set(get_online_users())
    except Exception:  # noqa: BLE001
        logger.warning("get online users failed", exc_info=True)
        return set()


def list_user_rooms(user, ai_enabled: bool = False) -> list:
    """我的会话列表：公共聊天室 → AI 助手（开关开启时）→ 有消息的私聊/AI 会话。

    私聊/AI 会话排序：未读优先，再按最后消息时间倒序（零额外排序成本，
    会话表冗余了 last_message/last_message_time）。
    """
    public_room = get_public_room()
    online_pks = online_user_pks()
    result = [room_to_dict(public_room, user, 0, online_pks)]

    fixed_ids = {public_room.pk}
    if ai_enabled:
        ai_room = get_or_create_ai_room(user)
        fixed_ids.add(ai_room.pk)
        unread = (
            ChatRoomMember.objects.filter(room=ai_room, user_id=_user_pk(user))
            .values_list("unread_count", flat=True)
            .first()
        )
        result.append(room_to_dict(ai_room, user, unread or 0, online_pks))

    member_rows = (
        ChatRoomMember.objects.filter(user_id=_user_pk(user))
        .select_related("room")
        .filter(room__is_active=True, room__last_message_time__isnull=False)
        .exclude(room_id__in=fixed_ids)
    )
    unread_map = {row.room_id: row.unread_count for row in member_rows}
    rooms = [row.room for row in member_rows]
    rooms.sort(
        key=lambda room: (
            unread_map.get(room.pk, 0) <= 0,
            -(room.last_message_time.timestamp() if room.last_message_time else 0),
            -room.pk,
        )
    )
    result.extend(room_to_dict(room, user, unread_map.get(room.pk, 0), online_pks) for room in rooms[:ROOM_LIST_LIMIT])
    return result


def recent_contacts(user, limit: int = CONTACT_LIMIT) -> list:
    """最近在线联系人：按最近活跃倒序（在线优先），含在线态。

    数据源 = UserSession.last_active（登录即登记，WS/HTTP 会话统一），
    在线态 = message.utils 在线快照（WS 心跳口径）。
    """
    from system.models import UserInfo, UserSession

    online_pks = online_user_pks()
    rows = list(
        UserSession.objects.exclude(creator_id=_user_pk(user))
        .values("creator_id")
        .annotate(active_at=Max("last_active"))
        .order_by("-active_at")[: limit * 2]
    )
    last_active = {row["creator_id"]: row["active_at"] for row in rows if row["creator_id"]}
    users = {item.pk: item for item in UserInfo.objects.filter(pk__in=list(last_active), is_active=True)}
    result = []
    for pk, active_at in last_active.items():
        item = users.get(pk)
        if item is None:
            continue
        brief = user_brief(item)
        brief["online"] = pk in online_pks
        brief["last_active"] = active_at.isoformat() if active_at else ""
        result.append(brief)
    # 稳定排序两趟：先按最近活跃倒序，再把在线的整体提到前面
    result.sort(key=lambda item: item["last_active"], reverse=True)
    result.sort(key=lambda item: not item["online"])
    return result[:limit]


# ---------------------------------------------------------------- @提及


def parse_mentions(content: str) -> list:
    """全位置、多目标解析 @提及（去重保序）。"""
    seen = []
    for name in MENTION_PATTERN.findall(content or ""):
        if name and name not in seen:
            seen.append(name)
    return seen


def mention_users(content: str, exclude_username: str = "") -> list:
    """把 @提及解析为在用用户对象（跳过自己与不存在的用户名）。"""
    from system.models import UserInfo

    names = [name for name in parse_mentions(content) if name != exclude_username]
    if not names:
        return []
    return list(UserInfo.objects.filter(username__in=names, is_active=True))


def new_client_msg_id() -> str:
    """服务端侧消息幂等键（AI/系统消息落库用，避免与客户端键空间混淆）。"""
    return uuid.uuid4().hex
