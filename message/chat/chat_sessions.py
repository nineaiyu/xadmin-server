#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""会话域：未读计数 / 已读游标 / 会话列表 / 最近联系人。

自 messaging 拆出（消息域 vs 会话域 vs 回应域分文件，控制单文件体量）。
"""

from typing import Any

from django.db.models import F, Max, Q

from message.models import ChatMessage, ChatRoom, ChatRoomMember

from .chat_ops import _user_pk, user_brief
from .chat_room_ops import get_or_create_ai_room, get_public_room
from .chat_rooms import UNSET, online_user_pks, prefetch_room_context, room_to_dict

# 会话列表 / 联系人默认条数
ROOM_LIST_LIMIT = 100
CONTACT_LIMIT = 100


def bump_unread(room: ChatRoom, exclude_pk: Any = None) -> dict[str, Any]:
    """未读计数 +1（除发送者），返回 {user_pk: unread_count}（仅私聊/AI 会话）。"""
    if room.room_type == ChatRoom.RoomType.PUBLIC:
        return {}
    queryset = room.members.all()
    if exclude_pk is not None:
        queryset = queryset.exclude(user_id=exclude_pk)
    if not queryset.update(unread_count=F("unread_count") + 1):
        return {}
    return dict(room.members.exclude(user_id=exclude_pk).values_list("user_id", "unread_count"))


def mark_read(room: ChatRoom, user: Any, message_id: Any = None) -> int:
    """清零未读并推进已读游标，返回最新游标（公共房间返回 0）。

    游标无推进且未读已是 0 时跳过写库：聊天页停留期间会反复触发已读上报
    （切换会话 / 会话内来消息），状态无变化时不再产生空转 UPDATE。
    """
    if room.room_type == ChatRoom.RoomType.PUBLIC:
        return 0
    latest = message_id or ChatMessage.objects.filter(room=room).aggregate(Max("id"))["id__max"] or 0
    member = ChatRoomMember.objects.filter(room=room, user_id=_user_pk(user)).first()
    if member is None:
        return 0
    cursor: int = max(member.last_read_id or 0, int(latest))
    if cursor == (member.last_read_id or 0) and member.unread_count == 0:
        return cursor
    member.last_read_id = cursor
    member.unread_count = 0
    member.save(update_fields=["last_read_id", "unread_count", "updated_time"])
    return cursor


# ---------------------------------------------------------------- 列表


def list_user_rooms(user: Any, ai_enabled: bool = False) -> list[Any]:
    """我的会话列表：公共聊天室 → AI 助手（开关开启时）→ 有消息的私聊/AI + 全部群聊。

    会话排序：未读优先，再按最后消息时间倒序（零额外排序成本，会话表冗余了
    last_message/last_message_time；群聊创建即入列，时间缺省按 0 处理）。
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

    # 私聊/AI 只列有消息的会话（避免空会话占位）；群聊创建即入列表（尚无消息也应可见）
    member_rows = (
        ChatRoomMember.objects.filter(user_id=_user_pk(user))
        .select_related("room")
        .filter(room__is_active=True)
        .filter(Q(room__last_message_time__isnull=False) | Q(room__room_type=ChatRoom.RoomType.GROUP))
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
    # 列表批量预取：对端/成员预览/成员数 3 次查询覆盖整页（替代逐房间 N 次）
    page_rooms = rooms[:ROOM_LIST_LIMIT]
    peer_map, preview_map, count_map = prefetch_room_context(page_rooms, user)
    result.extend(
        room_to_dict(
            room,
            user,
            unread_map.get(room.pk, 0),
            online_pks,
            peer=peer_map.get(room.pk, UNSET),
            member_preview=preview_map.get(room.pk, UNSET),
            member_count=count_map.get(room.pk, UNSET),
        )
        for room in page_rooms
    )
    return result


def recent_contacts(user: Any, limit: int = CONTACT_LIMIT) -> list[Any]:
    """最近在线联系人：按最近活跃倒序（在线优先），含在线态。

    数据源 = UserSession.last_active（登录即登记，WS/HTTP 会话统一），
    在线态 = message.ws.utils 在线快照（WS 心跳口径）。
    """
    from identity.models import UserInfo, UserSession

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
