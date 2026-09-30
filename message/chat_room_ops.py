#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天室房间开通与群组管理（自 message/chat.py 平移）。

对外的既有调用面（``from message import chat as chat_service`` 的属性访问）
经 chat.py 再导出保持不变。
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext_lazy as _

from message.chat_ops import _normalize_user_pks, _user_pk
from message.models import (
    MAX_GROUP_MEMBERS,
    ChatRoom,
    ChatRoomMember,
    ai_room_key,
    group_room_key,
    private_room_key,
)

# ------------------------------------------------------- 房间开通与群组管理


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


def create_group(owner, name: str, member_pks) -> ChatRoom:
    """创建多人群聊：名称必填、成员（含创建者）至少 2 人、上限 MAX_GROUP_MEMBERS。

    创建者为群主；成员只接受在用用户，任一非法/失效成员整体拒绝（避免半成品群）。
    """
    from system.models import UserInfo

    name = (name or "").strip()
    if not name:
        raise DjangoValidationError(_("Group name cannot be empty"))
    if len(name) > 64:
        raise DjangoValidationError(_("Group name is too long (max 64 characters)"))
    owner_pk = _user_pk(owner)
    pks = [pk for pk in _normalize_user_pks(member_pks) if pk != owner_pk]
    if not pks:
        raise DjangoValidationError(_("Select at least one member"))
    members = list(UserInfo.objects.filter(pk__in=pks, is_active=True))
    if len(members) != len(pks):
        raise DjangoValidationError(_("Some selected members are unavailable"))
    if len(members) + 1 > MAX_GROUP_MEMBERS:
        raise DjangoValidationError(_("Group members exceed the limit of {}").format(MAX_GROUP_MEMBERS))
    room = ChatRoom.objects.create(
        room_key=group_room_key(), room_type=ChatRoom.RoomType.GROUP, name=name, owner_id=owner_pk
    )
    ChatRoomMember.objects.get_or_create(room=room, user_id=owner_pk)
    # 入群顺序按 pk 固定：群主退出时「最早加入」的判定与插入顺序可复现
    for member in sorted(members, key=lambda user: user.pk):
        ChatRoomMember.objects.get_or_create(room=room, user=member)
    return room


def group_room_or_deny(room_id, user) -> ChatRoom:
    """群聊会话（fail-closed）：非群聊或非成员一律按「房间不存在」拒绝。"""
    room = ChatRoom.objects.filter(pk=room_id, is_active=True, room_type=ChatRoom.RoomType.GROUP).first()
    if room is None or not ChatRoomMember.objects.filter(room=room, user_id=_user_pk(user)).exists():
        raise DjangoValidationError(_("Chat room not found"))
    return room


def _require_group_owner(room, user) -> None:
    if room.owner_id != _user_pk(user):
        raise DjangoValidationError(_("Only the group owner can perform this operation"))


def rename_group(room_id, user, name: str) -> ChatRoom:
    """群主改名（成员态校验 + 群主门槛）。"""
    room = group_room_or_deny(room_id, user)
    _require_group_owner(room, user)
    name = (name or "").strip()
    if not name:
        raise DjangoValidationError(_("Group name cannot be empty"))
    if len(name) > 64:
        raise DjangoValidationError(_("Group name is too long (max 64 characters)"))
    room.name = name
    room.save(update_fields=["name", "updated_time"])
    return room


def add_group_members(room_id, user, member_pks) -> ChatRoom:
    """群主拉人入群：已在内/失效成员静默跳过；超上限整体拒绝。"""
    from system.models import UserInfo

    room = group_room_or_deny(room_id, user)
    _require_group_owner(room, user)
    owner_pk = _user_pk(user)
    existing = set(room.members.values_list("user_id", flat=True))
    pks = [pk for pk in _normalize_user_pks(member_pks) if pk not in existing and pk != owner_pk]
    if not pks:
        return room
    new_members = list(UserInfo.objects.filter(pk__in=pks, is_active=True))
    if len(existing) + len(new_members) > MAX_GROUP_MEMBERS:
        raise DjangoValidationError(_("Group members exceed the limit of {}").format(MAX_GROUP_MEMBERS))
    for member in sorted(new_members, key=lambda user: user.pk):
        ChatRoomMember.objects.get_or_create(room=room, user=member)
    return room


def remove_group_members(room_id, user, member_pks) -> ChatRoom:
    """群主移除成员：成员行（含未读游标）删除；群主不能被移除。"""
    room = group_room_or_deny(room_id, user)
    _require_group_owner(room, user)
    pks = _normalize_user_pks(member_pks)
    if _user_pk(user) in pks:
        raise DjangoValidationError(_("The group owner cannot be removed"))
    if pks:
        room.members.filter(user_id__in=pks).delete()
    return room


def leave_group(room_id, user) -> ChatRoom:
    """退出群聊：群主退出自动转让给最早加入成员；最后一人退出则房间软删。"""
    room = group_room_or_deny(room_id, user)
    room.members.filter(user_id=_user_pk(user)).delete()
    remaining = list(room.members.order_by("created_time", "pk").values_list("user_id", flat=True))
    if not remaining:
        room.is_active = False
        room.save(update_fields=["is_active", "updated_time"])
    elif room.owner_id == _user_pk(user):
        room.owner_id = remaining[0]
        room.save(update_fields=["owner_id", "updated_time"])
    return room


def accessible_room(room_id, user) -> ChatRoom:
    """按可访问性取房间（fail-closed）：公共全员可进、私聊/群聊仅成员、AI 仅归属者。"""
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
