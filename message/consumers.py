#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天室 WebSocket 通道：`ws/chat/` → ChatNotify。

与历史 `ws/message/<group>/<username>`（MessageNotify）的关系：
- 旧通道保持原样（全站通知推送 + 登录日志/会话登记），聊天室页面已切到本通道；
- 本通道**不登记 UserSession / 不写登录日志**：会话登记由全局通知连接唯一负责，
  避免在线列表把同一会话数成两条（`test_online_stats.py` 口径不回归）；
- 组名显式：公共广播组 `chat_room_public` + 用户聊天组 `chat_user_{pk}`，
  不再依赖「URL 第二段用户名是否是本人」这条隐式规则。

上行 action（protocol.MessageAction）：
- `chat_message {room_id, content, client_msg_id}` → 落库后广播（幂等：同 client_msg_id 不重复落库/广播）；
- `chat_recall {message_id}` → 本人在 2 分钟内撤回，双方同步；
- `chat_read {room_id}` → 清零未读并回执最新已读游标。

下行 action：`chat_message` / `chat_recall` / `chat_read` / `chat_unread`。
"""

import asyncio

from channels.db import database_sync_to_async
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

from common.core.config import UserConfig
from common.utils import get_logger
from message import chat as chat_service
from message.base import AsyncJsonWebsocket
from message.models import ChatRoom, ChatRoomMember
from message.protocol import MessageAction
from message.utils import (
    async_push_message,
    get_chat_user_group_name,
    get_public_chat_group_name,
    room_event_groups,
)

logger = get_logger(__name__)


def unread_rows(user) -> list:
    """本人未读会话（room_id, unread_count）列表（连接建立时的首屏对齐）。"""
    return list(ChatRoomMember.objects.filter(user=user, unread_count__gt=0).values_list("room_id", "unread_count"))


def can_push_chat(user_pk) -> bool:
    """用户是否允许聊天消息站内信提醒（个人偏好 PUSH_CHAT_MESSAGE）。

    必须是独立同步函数：在协程里直接取 `UserConfig(pk).PUSH_CHAT_MESSAGE`
    会触发同步 ORM 查询（Django SynchronousOnlyOperation）。
    """
    return bool(UserConfig(user_pk).PUSH_CHAT_MESSAGE)


def has_chat_permission(user) -> bool:
    """聊天通道准入（fail-closed）：超管放行；其余用户需持有聊天室会话列表权限。

    与 HTTP 侧同一套权限数据（菜单权限点 `list:ChatRoom` → 路径 `api/chat/room$`），
    未被授予聊天室页面的用户即使拿到 WS 地址也无法接入。
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    from common.core.permission import get_menu_pk, get_user_permission

    data = get_user_permission(user, "GET")
    return bool(get_menu_pk(data, "/api/chat/room"))


class ChatNotify(AsyncJsonWebsocket):
    """聊天室专用连接：一条连接同时承载公共广播 + 私聊/AI 定向 + 未读推送。"""

    def __init__(self, *args, **kwargs):
        super().__init__(args, kwargs)
        self.user = None
        self.disconnected = True
        self.group_name = ""  # 本人聊天组（私聊/AI/未读）
        self.public_group = ""  # 公共聊天室广播组

    # ------------------------------------------------------------ 连接生命周期

    async def connect(self):
        self.user = self.scope["user"]
        if not self.user:
            logger.error("chat connect rejected: not authenticated")
            await asyncio.sleep(3)
            await self.close(4401)
            return
        if not await database_sync_to_async(has_chat_permission)(self.user):
            logger.warning("chat connect rejected: no permission user=%s", self.user)
            await self.close(4403)
            return

        self.group_name = get_chat_user_group_name(self.user.pk)
        self.public_group = get_public_chat_group_name()
        await self.channel_layer.group_add(self.public_group, self.channel_name)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        self.disconnected = False
        await self.accept()
        # 首屏未读对齐：连接建立即下发本人未读快照（前端左列表红点）
        await self.push_unread_snapshot()

    async def disconnect(self, close_code):
        self.disconnected = True
        for group in (self.public_group, self.group_name):
            if group:
                await self.channel_layer.group_discard(group, self.channel_name)
        logger.info("chat disconnect: %s", self.user)

    async def ping(self, event):
        """心跳：同时续期两个分组（基类只续期 self.group_name）。

        两个组名都不匹配个人推送组前缀，因此不会写入在线索引/在线列表。
        """
        for group in (self.public_group, self.group_name):
            if group:
                await self.channel_layer.update_active_layers(group, self.channel_name)
        event["data"] = "pong"
        await self._send_base(event)

    # ------------------------------------------------------------ 上行分发

    async def receive_json(self, action, data, content, **kwargs):
        match action:
            case MessageAction.CHAT_MESSAGE.value:
                await self.handle_send(data)
            case MessageAction.CHAT_RECALL.value:
                await self.handle_recall(data)
            case MessageAction.CHAT_READ.value:
                await self.handle_read(data)
            case _:
                logger.error("chat action unknown. so close. %s", content)
                await asyncio.sleep(3)
                await self.close()

    async def handle_send(self, data):
        try:
            room = await database_sync_to_async(chat_service.accessible_room)(data.get("room_id"), self.user)
            message, created = await database_sync_to_async(chat_service.create_message)(
                room,
                self.user,
                data.get("content"),
                client_msg_id=str(data.get("client_msg_id") or "")[:64],
            )
        except DjangoValidationError as exc:
            await self.send_base_json(MessageAction.CHAT_MESSAGE.value, code=1001, detail="; ".join(exc.messages))
            return

        payload = await database_sync_to_async(chat_service.message_payload)(message, room=room, sender=self.user)
        if not created:
            # 幂等命中（断线重发）：只回执给发送方，不重复广播/不重复计未读
            await self.send_base_json(MessageAction.CHAT_MESSAGE.value, data=payload)
            return

        await self.broadcast(room, MessageAction.CHAT_MESSAGE.value, payload)
        unread = await database_sync_to_async(chat_service.bump_unread)(room, self.user.pk)
        for user_pk, unread_count in unread.items():
            await self.channel_layer.group_send(
                get_chat_user_group_name(user_pk),
                {"type": MessageAction.CHAT_UNREAD.value, "data": {"room_id": room.pk, "unread_count": unread_count}},
            )
        await self.notify_room(room, payload)

    async def handle_recall(self, data):
        try:
            message = await database_sync_to_async(chat_service.recall_message)(self.user, data.get("message_id"))
        except DjangoValidationError as exc:
            await self.send_base_json(MessageAction.CHAT_RECALL.value, code=1001, detail="; ".join(exc.messages))
            return
        room = await database_sync_to_async(ChatRoom.objects.get)(pk=message.room_id)
        payload = {"message_id": message.pk, "id": message.pk, "room_id": message.room_id, "operator_pk": self.user.pk}
        await self.broadcast(room, MessageAction.CHAT_RECALL.value, payload)

    async def handle_read(self, data):
        try:
            room = await database_sync_to_async(chat_service.accessible_room)(data.get("room_id"), self.user)
        except DjangoValidationError as exc:
            await self.send_base_json(MessageAction.CHAT_READ.value, code=1001, detail="; ".join(exc.messages))
            return
        last_read_id = await database_sync_to_async(chat_service.mark_read)(room, self.user, data.get("last_read_id"))
        await self.send_base_json(
            MessageAction.CHAT_READ.value, data={"room_id": room.pk, "last_read_id": last_read_id}
        )

    # ------------------------------------------------------------ 下行事件

    async def chat_recall(self, event):
        await self._send_base(event)

    async def chat_unread(self, event):
        await self._send_base(event)

    # ------------------------------------------------------------ 内部

    async def broadcast(self, room, message_type: str, payload: dict):
        """按房间类型广播（目标组口径见 message.utils.room_event_groups，REST 侧同源）。"""
        groups = await database_sync_to_async(room_event_groups)(room)
        for group in groups:
            await self.channel_layer.group_send(group, {"type": message_type, "data": payload})

    async def push_unread_snapshot(self):
        """连接建立时下发本人未读快照（私聊/AI 会话逐条）。"""
        rows = await database_sync_to_async(unread_rows)(self.user)
        for room_id, unread_count in rows:
            await self.send_base_json(
                MessageAction.CHAT_UNREAD.value, data={"room_id": room_id, "unread_count": unread_count}
            )

    async def notify_room(self, room, payload: dict):
        """站内信提醒：私聊提醒对端（对端不在聊天室时）、公共房间提醒被 @ 的用户。"""
        if room.room_type == ChatRoom.RoomType.PUBLIC:
            await self.notify_mentions(payload)
            return
        if room.room_type != ChatRoom.RoomType.PRIVATE:
            return
        peer_pks = await database_sync_to_async(chat_service.room_member_pks)(room)
        for user_pk in peer_pks:
            if user_pk == self.user.pk:
                continue
            if await self.chat_channel_alive(user_pk):
                # 对端聊天室页面在线：消息已实时送达，不再弹站内信（避免重复提醒）
                continue
            if not await database_sync_to_async(can_push_chat)(user_pk):
                continue
            await async_push_message(
                user_pk,
                {
                    "title": str(_("New private message from {}").format(payload.get("sender_name") or "")),
                    "message": payload.get("content", ""),
                    "level": "info",
                    "notice_type": {"label": str(_("Private chat")), "value": 0},
                    "message_type": "chat_private",
                    "room_id": room.pk,
                    "sender_pk": self.user.pk,
                },
            )

    async def notify_mentions(self, payload: dict):
        """@提及站内信：全位置、多目标（不含自己）；受用户 PUSH_CHAT_MESSAGE 偏好约束。"""
        content = payload.get("content") or ""
        try:
            targets = await database_sync_to_async(chat_service.mention_users)(content, self.user.username)
        except Exception:  # noqa: BLE001 提及通知属增强链路，失败不影响消息投递
            logger.warning("parse mentions failed", exc_info=True)
            return
        for target in targets:
            if not await database_sync_to_async(can_push_chat)(target.pk):
                continue
            await async_push_message(
                target.pk,
                {
                    "title": str(_("User {} mentioned you in the chat room").format(self.user.username)),
                    "message": content,
                    "level": "info",
                    "notice_type": {"label": str(_("Chat room")), "value": 0},
                    "message_type": "chat_message",
                    "room_id": payload.get("room_id"),
                    "sender_pk": self.user.pk,
                },
            )

    async def chat_channel_alive(self, user_pk) -> bool:
        """对端是否正开着聊天室页面（存在活跃聊天连接）。查询失败按「不在线」处理。"""
        try:
            channels = await self.channel_layer.get_layers(get_chat_user_group_name(user_pk))
            return bool(channels)
        except Exception:  # noqa: BLE001
            return False
