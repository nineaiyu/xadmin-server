#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天室领域服务：会话开通 / 消息落库 / 未读游标 / 联系人 / @提及。

分层约定：
- 本模块只做同步 DB 与纯函数逻辑（REST 视图与 WS consumer 共用同一套语义）：
  WS consumer 经 `database_sync_to_async` 调用，REST 视图直接调用；
- 消息广播（channel layer）不在本模块，由 message/utils.py 与 ws/chat consumer 负责，
  避免同步/异步边界混杂；
- 可访问性一律 fail-closed：非成员访问私聊、非归属访问他人 AI 会话均按「房间不存在」拒绝。
"""

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Max, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

# 再导出（PEP 484 显式形式，跨模块调用面保持不变）：附件链路 / chat_service 调用面 / 房间与群组管理 / 会话列表构造 / 撤回审计
from message.attachments import ATTACHMENT_MESSAGE_TYPES as ATTACHMENT_MESSAGE_TYPES
from message.attachments import AttachmentUploadError as AttachmentUploadError
from message.attachments import attachment_extra as attachment_extra
from message.attachments import attachment_payload as attachment_payload
from message.attachments import mark_attachment_used as mark_attachment_used
from message.attachments import resolve_sender_attachment as resolve_sender_attachment
from message.attachments import store_message_attachment as store_message_attachment
from message.attachments import validate_attachment_kind as validate_attachment_kind
from message.chat_ops import _normalize_user_pks as _normalize_user_pks
from message.chat_ops import _user_pk as _user_pk
from message.chat_ops import avatar_url as avatar_url
from message.chat_ops import clean_expired_history as clean_expired_history
from message.chat_ops import display_name as display_name
from message.chat_ops import mention_users as mention_users
from message.chat_ops import new_client_msg_id as new_client_msg_id
from message.chat_ops import parse_mentions as parse_mentions
from message.chat_ops import store_chat_notices as store_chat_notices
from message.chat_ops import user_brief as user_brief
from message.chat_room_ops import accessible_room as accessible_room
from message.chat_room_ops import add_group_members as add_group_members
from message.chat_room_ops import create_group as create_group
from message.chat_room_ops import get_or_create_ai_room as get_or_create_ai_room
from message.chat_room_ops import get_or_create_private_room as get_or_create_private_room
from message.chat_room_ops import get_or_create_private_room_by_pk as get_or_create_private_room_by_pk
from message.chat_room_ops import get_public_room as get_public_room
from message.chat_room_ops import group_room_or_deny as group_room_or_deny
from message.chat_room_ops import leave_group as leave_group
from message.chat_room_ops import remove_group_members as remove_group_members
from message.chat_room_ops import rename_group as rename_group
from message.chat_room_ops import room_member_pks as room_member_pks
from message.chat_rooms import UNSET as UNSET
from message.chat_rooms import online_user_pks as online_user_pks
from message.chat_rooms import prefetch_room_context as prefetch_room_context
from message.chat_rooms import room_to_dict as room_to_dict
from message.models import (
    AI_MAX_CONTENT_LENGTH,
    MAX_CONTENT_LENGTH,
    REACTION_EMOJI_MAX_LENGTH,
    REACTION_MAX_EMOJI_KEYS,
    REACTION_MAX_USERS_PER_EMOJI,
    REACTION_MESSAGE_TYPES,
    RECALL_WINDOW_MINUTES,
    ChatMessage,
    ChatRoom,
    ChatRoomMember,
)

# 再导出：撤回审计常量与快照写入（调用面/测试沿用 chat_service 常量）
from message.recall_audit import RECALL_AUDIT_MODULE as RECALL_AUDIT_MODULE
from message.recall_audit import write_recall_snapshot as write_recall_snapshot

logger = get_logger(__name__)

# 会话列表 / 联系人默认条数
ROOM_LIST_LIMIT = 100
CONTACT_LIMIT = 100


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
    sender: Any,
    content: str,
    message_type: str = ChatMessage.MessageType.TEXT,
    client_msg_id: str = "",
    extra: dict[str, Any] | None = None,
    attachment: Any = None,
) -> tuple[Any, ...]:
    """落库一条消息，返回 (message, created)。

    幂等：同一发送者 + 同一 client_msg_id 命中已有消息时直接返回旧消息
    （断线重发 / 乐观上屏重复提交都不产生第二行）。
    AI / 系统消息放宽长度上限（模型回答常超用户输入上限）。
    附件消息（image / file）必须携带 attachment（已做过归属校验的上传件），
    内容缺省取文件名；落库同时把附件由临时态转正（见 message/attachments.py）。
    """
    if message_type in ATTACHMENT_MESSAGE_TYPES:
        if attachment is None:
            raise DjangoValidationError(_("Attachment not found"))
        validate_attachment_kind(attachment, message_type)
        content = (content or "").strip() or attachment.filename
        extra = {"file": attachment_extra(attachment), **(extra or {})}
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
                attachment=attachment,
            )
            mark_attachment_used(attachment)
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


def message_payload(
    message: ChatMessage, room: Any = None, sender: Any = None, avatar_map: dict[str, Any] | None = None
) -> dict[str, Any]:
    """消息 → 前端渲染载荷（WS 广播 / REST 历史共用同一形状）。

    room / sender / avatar_map 为可选预取参数：批量场景（历史列表）传入 avatar_map
    可避免逐条查询发送者头像。
    """
    if avatar_map is None:
        if sender is None and message.sender_id:
            from identity.models import UserInfo

            sender = UserInfo.objects.filter(pk=message.sender_id).first()
        avatar = avatar_url(sender)
    else:
        avatar = avatar_map.get(message.sender_id, "")
    room_type = ""
    if room is not None:
        room_type = room.room_type
    extra = dict(message.extra or {})
    file_payload = attachment_payload(message)
    if file_payload is not None:
        # 附件取件 URL 由消息 pk 派生（历史/广播同形状），随 extra["file"] 一起下发
        extra["file"] = file_payload
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
        "extra": extra,
    }


def sender_avatar_map(messages: list[Any]) -> dict[str, Any]:
    """一批消息的发送者头像映射（一次查询，供历史列表/广播批量使用）。"""
    from identity.models import UserInfo

    pks = {message.sender_id for message in messages if message.sender_id}
    if not pks:
        return {}
    result = {}
    for user in UserInfo.objects.filter(pk__in=pks).only("pk", "avatar"):
        result[user.pk] = avatar_url(user)
    return result


serialize_message = message_payload  # 兼容别名（WS 广播语义）


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


def recall_message(user: Any, message_id: Any) -> ChatMessage:
    """撤回：仅本人、窗口内、未撤回（超窗/越权返回可读校验错误）。

    读-判-写整体包进事务并以行锁取出消息：并发/重复撤回时后到者在锁内
    重读到「已撤回」即被拒，审计快照与撤回状态各只落一次（不加锁时两个
    事务都能通过校验，会产生双快照并双双落库）。
    """
    with transaction.atomic():
        message: ChatMessage | None = ChatMessage.objects.select_for_update().filter(pk=message_id).first()
        if message is None:
            raise DjangoValidationError(_("Message not found"))
        if message.sender_id != _user_pk(user):
            raise DjangoValidationError(_("Only the sender can recall the message"))
        if message.is_recalled:
            raise DjangoValidationError(_("Message already recalled"))
        created = message.created_time or timezone.now()
        if timezone.now() - created > timezone.timedelta(minutes=RECALL_WINDOW_MINUTES):
            raise DjangoValidationError(
                _("Messages can only be recalled within {} minutes").format(RECALL_WINDOW_MINUTES)
            )
        write_recall_snapshot(message, user)
        message.is_recalled = True
        message.recalled_time = timezone.now()
        message.content = ""
        message.save(update_fields=["is_recalled", "recalled_time", "content", "updated_time"])
    return message


# ---------------------------------------------------------------- 历史 / 附件取件


def can_recall_for(message: ChatMessage, user: Any) -> bool:
    """撤回资格：仅本人、未撤回、撤回窗口内（与 recall_message 同口径的读侧判定）。

    REST 历史（history_messages）与 WS 新消息的发送者定向帧共用本判定，
    保证两条下发链路的撤回资格完全一致。
    """
    if message.is_recalled or not message.sender_id or message.sender_id != user.pk:
        return False
    created = message.created_time or timezone.now()
    within_window: bool = timezone.now() - created <= timezone.timedelta(minutes=RECALL_WINDOW_MINUTES)
    return within_window


def history_messages(room: ChatRoom, user: Any, before_id: Any = None, limit: int = 20) -> dict[str, Any]:
    """历史消息游标分页：`before_id` 倒序拉取（响应内按时间正序），附带撤回资格。

    `room` 须为调用方可访问的房间（视图层先经 accessible_room 校验）；`limit`
    由视图层完成解析与夹取。载荷含房间快照与 has_more 游标标志。
    """
    queryset = ChatMessage.objects.filter(room=room).select_related("room")
    if before_id:
        queryset = queryset.filter(id__lt=int(before_id))
    rows = list(queryset.order_by("-id")[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    avatar_map = sender_avatar_map(rows)

    messages = []
    for message in rows:
        payload = message_payload(message, room=room, avatar_map=avatar_map)
        payload["can_recall"] = can_recall_for(message, user)
        messages.append(payload)
    messages.reverse()  # 前端按时间正序渲染
    return {
        "results": messages,
        "has_more": has_more,
        "room": room_to_dict(room, user, 0, online_user_pks()),
    }


def get_attachment_message(message_pk: Any, user: Any) -> ChatMessage:
    """附件取件定位：消息存在 → 房间可访问（fail-closed）→ 未撤回。

    消息不存在抛「Message not found」、已撤回抛「File not found」（错误文案
    与原视图口径一致，不合并——存在性探测面保持原样）；非可访问者经
    accessible_room 抛「房间不存在」可读校验错误。
    """
    message: ChatMessage | None = ChatMessage.objects.select_related("attachment").filter(pk=message_pk).first()
    if message is None:
        raise DjangoValidationError(_("Message not found"))
    accessible_room(message.room_id, user)
    if message.is_recalled:
        raise DjangoValidationError(_("File not found"))
    return message


# ---------------------------------------------------------------- 表情回应


def toggle_reaction(user: Any, message_pk: Any, emoji: Any, op: Any) -> tuple[Any, ...] | None:
    """表情回应落库（extra["reactions"] = {emoji: [user_pk, ...]}，不建新表）。

    返回 ``(房间, 消息 pk, 全量 reactions, 广播时刻 epoch 秒)``，调用方据此向房间
    广播 `chat_reaction` 帧（全量表，客户端整体替换）。

    以下情形**静默忽略**（返回 None，不报错不广播）：
    - 消息不存在 / 已撤回：撤回即冻结交互（与「撤回后附件不可取件」同口径）；
    - 操作者非房间可访问者：fail-closed，与 accessible_room 同源（不区分
      「房间不存在」与「无权」，避免探测）；
    - 机器消息（ai / system）：回应没有对象语义，前端也不提供入口；
    - op 落空（重复 add 幂等不重记 / 移除不存在的回应）：仍返回当前全量表，
      供广播把 stale 客户端拉齐。

    emoji 为空 / 超长、op 非法、回应数超上限（模型层 REACTION_* 常量）抛可读
    校验错误，由 consumer 回执 1001。

    并发：select_for_update 行锁串行化同一消息的 extra 读改写——JSONField 没有
    字段级原子操作（F 表达式只覆盖数值/列表顶层），不加锁时两个用户同时回应会
    后写覆盖前写（丢回应）。
    """
    emoji = str(emoji or "").strip()
    op = str(op or "").strip().lower()
    if not emoji:
        raise DjangoValidationError(_("Reaction emoji cannot be empty"))
    if len(emoji) > REACTION_EMOJI_MAX_LENGTH:
        raise DjangoValidationError(
            _("Reaction emoji is too long (max {} characters)").format(REACTION_EMOJI_MAX_LENGTH)
        )
    if op not in ("add", "remove"):
        raise DjangoValidationError(_("Invalid reaction operation"))
    try:
        message_pk = int(message_pk)
    except (TypeError, ValueError):
        # 上行 pk 非法按「消息不存在」静默忽略（不回错误帧，避免探测面）
        return None
    with transaction.atomic():
        message = ChatMessage.objects.select_for_update().filter(pk=message_pk).first()
        if message is None or message.is_recalled or message.message_type not in REACTION_MESSAGE_TYPES:
            return None
        try:
            accessible_room(message.room_id, user)
        except DjangoValidationError:
            return None
        user_pk = _user_pk(user)
        extra = dict(message.extra or {})
        reactions = dict(extra.get("reactions") or {})
        members = list(reactions.get(emoji) or [])
        changed = False
        if op == "add":
            if user_pk not in members:
                if emoji not in reactions and len(reactions) >= REACTION_MAX_EMOJI_KEYS:
                    raise DjangoValidationError(
                        _("Too many emojis on this message (max {})").format(REACTION_MAX_EMOJI_KEYS)
                    )
                if len(members) >= REACTION_MAX_USERS_PER_EMOJI:
                    raise DjangoValidationError(
                        _("Too many users reacted with this emoji (max {})").format(REACTION_MAX_USERS_PER_EMOJI)
                    )
                reactions[emoji] = [*members, user_pk]
                changed = True
        else:
            # 只能移除自己：载荷不含目标用户字段，实现上即无法替他人移除；
            # 移除不存在的回应（自己不在列表）为幂等落空，不产生写与广播
            if user_pk in members:
                remaining = [pk for pk in members if pk != user_pk]
                if remaining:
                    reactions[emoji] = remaining
                else:
                    reactions.pop(emoji, None)
                changed = True
        if changed:
            if reactions:
                extra["reactions"] = reactions
            else:
                extra.pop("reactions", None)  # 回应清空后移除键，extra 不留空壳
            message.extra = extra
            message.save(update_fields=["extra", "updated_time"])
    room = ChatRoom.objects.filter(pk=message.room_id).first()
    return room, message.pk, dict(extra.get("reactions") or {}), int(timezone.now().timestamp())


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
    在线态 = message.utils 在线快照（WS 心跳口径）。
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
