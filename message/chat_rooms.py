#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""会话列表构造（自 message/chat.py 拆出，控制单文件体量）。

- 列表批量上下文：私聊对端 / 群成员预览 / 群成员数一次预取（3 次查询），
  替代逐房间查询（原实现每私聊 1 次、每群 2 次，房间数即查询数）；
- 单房间调用（建群/改群/进房）仍走 room_to_dict 的缺省分支，行为不变。

分层约定：本模块只依赖 models / chat_ops 与 system 服务，不反向依赖 chat.py；
chat.py 再导出本模块符号，历史调用面保持不变。
"""

from common.utils import get_logger
from message.chat_ops import _user_pk, user_brief
from message.models import GROUP_MEMBERS_PREVIEW, ChatRoom, ChatRoomMember

logger = get_logger(__name__)

# 未预取时与「预取结果为 None」区分（私聊无对端是合法状态）
UNSET = object()


def online_user_pks() -> set:
    """在线用户快照（失败降级为空集：在线态属增强展示，不应阻断会话列表）。"""
    try:
        from message.utils import get_online_users

        return set(get_online_users())
    except Exception:  # noqa: BLE001
        logger.warning("get online users failed", exc_info=True)
        return set()


def _fetch_peer(room: ChatRoom, user):
    """单房间私聊对端（列表批量路径见 prefetch_room_context）。"""
    from system.models import UserInfo

    peer_obj = UserInfo.objects.filter(pk__in=room.members.exclude(user_id=_user_pk(user)).values("user_id")).first()
    return user_brief(peer_obj) if peer_obj is not None else None


def prefetch_room_context(rooms, user) -> tuple:
    """列表批量上下文：返回 (peer_map, member_preview_map, member_count_map)。

    - 私聊对端：一次 IN 查询取全部对端用户；
    - 群成员预览 + 成员数：一次查询取成员行，Python 分组（每群取前
      GROUP_MEMBERS_PREVIEW 人 + 计数），避免每群 2 次查询。
    """
    from system.models import UserInfo

    private_ids = [room.pk for room in rooms if room.room_type == ChatRoom.RoomType.PRIVATE]
    group_ids = [room.pk for room in rooms if room.room_type == ChatRoom.RoomType.GROUP]
    peer_map: dict = {}
    preview_map: dict = {}
    count_map: dict = {}

    if private_ids:
        peer_rows = list(
            ChatRoomMember.objects.filter(room_id__in=private_ids)
            .exclude(user_id=_user_pk(user))
            .values_list("room_id", "user_id")
        )
        users = {item.pk: item for item in UserInfo.objects.filter(pk__in={user_id for _, user_id in peer_rows})}
        for room_id, user_id in peer_rows:
            item = users.get(user_id)
            peer_map[room_id] = user_brief(item) if item is not None else None

    if group_ids:
        member_rows = (
            ChatRoomMember.objects.filter(room_id__in=group_ids).select_related("user").order_by("created_time", "pk")
        )
        for row in member_rows:
            count_map[row.room_id] = count_map.get(row.room_id, 0) + 1
            members = preview_map.setdefault(row.room_id, [])
            if len(members) < GROUP_MEMBERS_PREVIEW:
                members.append(user_brief(row.user))

    return peer_map, preview_map, count_map


def room_to_dict(
    room: ChatRoom,
    user,
    unread_count: int = 0,
    online_pks: set | None = None,
    *,
    peer=UNSET,
    member_preview=UNSET,
    member_count=UNSET,
) -> dict:
    """会话列表行（前端左侧栏渲染契约）。

    群聊附加：群主/成员数与成员预览（前 GROUP_MEMBERS_PREVIEW 人，供头像堆叠与选人回显）。
    ``peer`` / ``member_preview`` / ``member_count`` 为列表批量预取结果（UNSET = 未预取，
    按单房间查询兜底）。
    """
    if peer is UNSET:
        peer = _fetch_peer(room, user) if room.room_type == ChatRoom.RoomType.PRIVATE else None
    if peer is not None and online_pks is not None:
        # 复制后再挂在线态：预取结果在多个房间间复用，不能就地改写共享 dict
        peer = {**peer, "online": peer.get("pk") in online_pks}
    payload = {
        "id": room.pk,
        "room_type": room.room_type,
        "room_key": room.room_key,
        "name": room.name,
        "peer": peer,
        "owner_pk": room.owner_id,
        "last_message": room.last_message,
        "last_message_time": room.last_message_time.isoformat() if room.last_message_time else "",
        "unread_count": unread_count,
    }
    if room.room_type == ChatRoom.RoomType.GROUP:
        if member_preview is UNSET:
            rows = list(room.members.select_related("user").order_by("created_time", "pk")[:GROUP_MEMBERS_PREVIEW])
            member_preview = [user_brief(row.user) for row in rows]
        if member_count is UNSET:
            member_count = room.members.count()
        payload["member_count"] = member_count
        payload["members"] = member_preview
        payload["is_owner"] = room.owner_id == _user_pk(user)
    return payload
