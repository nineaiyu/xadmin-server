#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天连接的站内信提醒与在线态判定（mixin，自 consumers 拆出）。

与 WS 协议层（上行分发 / 连接生命周期）解耦：本模块只负责「谁需要被提醒、
怎么提醒」——站内信推送（async_push_message）+ 通知中心落库兜底 + 聊天页面
在线态（channel layer 活跃连接查询）。混入 ChatNotify 使用（self.user /
self.channel_layer 来自宿主连接）与全局通知通道（notify.py 的 MessageNotify）
无关。
"""

import asyncio
from typing import Any

from channels.db import database_sync_to_async
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from message import chat as chat_service
from message.models import ChatRoom

from .utils import async_push_message, get_chat_user_group_name

logger = get_logger(__name__)


def batch_push_chat_enabled(user_pks: Any) -> dict[str, Any]:
    """批量读取「聊天消息站内信提醒」偏好（``{pk: bool}``）。

    与用户级配置同源（键 PUSH_CHAT_MESSAGE、系统默认 True）：群聊/私聊扇出
    一次批量读取替代逐人缓存读 + 逐人 database_sync_to_async 桥接。
    必须是独立同步函数：协程里直接取 ``UserConfig(pk).PUSH_CHAT_MESSAGE``
    会触发同步 ORM 查询（Django SynchronousOnlyOperation）。
    """
    from common.core.config import batch_user_config

    pks = list(dict.fromkeys(user_pks))
    if not pks:
        return {}
    return {pk: bool(value) for pk, value in batch_user_config(pks, "PUSH_CHAT_MESSAGE", True).items()}


class ChatNotifyMixin:
    """站内信提醒 + 在线态判定（notify_room / notify_group / notify_mentions / alive 族）。"""

    # 宿主连接（ChatNotify）提供的属性：类型面在此声明，实现由宿主注入
    user: Any
    channel_layer: Any

    async def notify_room(self, room: Any, payload: dict[str, Any]) -> None:
        """站内信提醒：私聊/群聊提醒不在聊天室的成员、公共房间提醒被 @ 的用户。"""
        if room.room_type == ChatRoom.RoomType.PUBLIC:
            await self.notify_mentions(payload)
            return
        if room.room_type == ChatRoom.RoomType.GROUP:
            await self.notify_group(room, payload)
            return
        if room.room_type != ChatRoom.RoomType.PRIVATE:
            return
        member_pks = await database_sync_to_async(chat_service.room_member_pks)(room)
        targets = await self.offline_notice_targets(member_pks)
        if not targets:
            return
        title = str(_("New private message from {}").format(payload.get("sender_name") or ""))
        # 逐用户串行 await 会让扇出延迟随人数线性增长：纯 Redis group_send 可并发
        await asyncio.gather(
            *(
                async_push_message(
                    user_pk,
                    {
                        "title": title,
                        "message": payload.get("content", ""),
                        "level": "info",
                        "notice_type": {"label": str(_("Private chat")), "value": 0},
                        "message_type": "chat_private",
                        "room_id": room.pk,
                        "sender_pk": self.user.pk,
                    },
                )
                for user_pk in targets
            )
        )
        # 落库兜底：目标不在聊天室页面（含离线）时通知中心可回看（群聊普通消息不落）
        await database_sync_to_async(chat_service.store_chat_notices)(
            targets,
            title=title,
            message=payload.get("content", ""),
            extra={
                "chat_room_id": str(room.pk),
                "chat_message_type": "chat_private",
                "sender_pk": str(self.user.pk),
            },
        )

    async def notify_group(self, room: Any, payload: dict[str, Any]) -> None:
        """群聊站内信：提醒不在聊天室页面的成员（在线者已实时收到，不重复提醒）。

        群聊普通消息**不落库**（高频消息落站内信会灌满通知中心）：实时提醒即可，
        离线回看走聊天室自身的未读游标；需要离线可见请使用 @提及（提及落库）。
        """
        member_pks = await database_sync_to_async(chat_service.room_member_pks)(room)
        targets = await self.offline_notice_targets(member_pks)
        if not targets:
            return
        title = str(_("New group message from {} in {}").format(payload.get("sender_name") or "", room.name))
        await asyncio.gather(
            *(
                async_push_message(
                    user_pk,
                    {
                        "title": title,
                        "message": payload.get("content", ""),
                        "level": "info",
                        "notice_type": {"label": str(_("Group chat")), "value": 0},
                        "message_type": "chat_group",
                        "room_id": room.pk,
                        "sender_pk": self.user.pk,
                    },
                )
                for user_pk in targets
            )
        )

    async def notify_mentions(self, payload: dict[str, Any]) -> None:
        """@提及站内信：全位置、多目标（不含自己）；受用户 PUSH_CHAT_MESSAGE 偏好约束。"""
        content = payload.get("content") or ""
        try:
            targets = await database_sync_to_async(chat_service.mention_users)(content, self.user.username)
        except Exception:  # noqa: BLE001 提及通知属增强链路，失败不影响消息投递
            logger.warning("parse mentions failed", exc_info=True)
            return
        pks = [target.pk for target in targets if target.pk != self.user.pk]
        if not pks:
            return
        enabled = await database_sync_to_async(batch_push_chat_enabled)(pks)
        title = str(_("User {} mentioned you in the chat room").format(self.user.username))
        pushed_pks = [target.pk for target in targets if target.pk != self.user.pk and enabled.get(target.pk, True)]
        if pushed_pks:
            await asyncio.gather(
                *(
                    async_push_message(
                        pk,
                        {
                            "title": title,
                            "message": content,
                            "level": "info",
                            "notice_type": {"label": str(_("Chat room")), "value": 0},
                            "message_type": "chat_message",
                            "room_id": payload.get("room_id"),
                            "sender_pk": self.user.pk,
                        },
                    )
                    for pk in pushed_pks
                )
            )
        # 落库兜底：与推送同批目标（离线/不在聊天室时通知中心可回看）
        await database_sync_to_async(chat_service.store_chat_notices)(
            pushed_pks,
            title=title,
            message=content,
            extra={
                "chat_room_id": str(payload.get("room_id") or ""),
                "chat_message_type": "chat_message",
                "sender_pk": str(self.user.pk),
            },
        )

    async def chat_channel_alive(self, user_pk: Any) -> bool:
        """对端是否正开着聊天室页面（存在活跃聊天连接）。查询失败按「不在线」处理。"""
        try:
            channels = await self.channel_layer.get_layers(get_chat_user_group_name(user_pk))
            return bool(channels)
        except Exception:  # noqa: BLE001
            return False

    async def chat_channels_alive(self, user_pks: Any) -> set[Any]:
        """批量判定这些用户是否正开着聊天室页面（扇出合并为一次 pipeline 往返）。

        逐人 ``get_layers`` 会退化成「每人 2 条命令 + 1 次往返」；channel layer
        的 ``get_layers_for_groups`` 把同节点全部 group 合并（生产 Redis 单节点
        下整个扇出一次往返）。查询失败按「不在线」处理（与单条口径一致）。
        """
        pks = list(dict.fromkeys(user_pks))
        if not pks:
            return set()
        groups = {pk: get_chat_user_group_name(pk) for pk in pks}
        batch_getter = getattr(self.channel_layer, "get_layers_for_groups", None)
        if batch_getter is None:  # 无批量原语的 layer：回退逐条查询（语义不变）
            online = set()
            for pk in pks:
                if await self.chat_channel_alive(pk):
                    online.add(pk)
            return online
        try:
            layers = await batch_getter(list(groups.values()))
        except Exception:  # noqa: BLE001
            return set()
        return {pk for pk, group in groups.items() if layers.get(group)}

    async def offline_notice_targets(self, user_pks: Any) -> list[Any]:
        """私聊/群聊扇出的站内信目标：不在聊天室页面 + 开启提醒偏好（批量预取）。

        在线态与偏好各一次批量读取（替代逐人 2 次往返的串行放大），候选顺序保留。
        """
        targets = [pk for pk in dict.fromkeys(user_pks) if pk != self.user.pk]
        if not targets:
            return []
        online = await self.chat_channels_alive(targets)
        pending = [pk for pk in targets if pk not in online]
        if not pending:
            return []
        enabled = await database_sync_to_async(batch_push_chat_enabled)(pending)
        return [pk for pk in pending if enabled.get(pk, True)]
