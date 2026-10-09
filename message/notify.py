#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : chat
# author : ly_13
# date : 6/2/2023
import asyncio
import os
from typing import Any

import aiofiles
from channels.db import database_sync_to_async
from django.conf import settings

from audit.services import UserLoginLog
from common.celery.utils import get_celery_task_log_path
from common.core.config import UserConfig
from common.utils import get_logger
from identity.services import (
    get_active_user_pk_by_username,
    login_success,
    register_user_session,
    websocket_session_logout,
)
from message.base import AsyncJsonWebsocket
from message.utils import async_push_message, get_user_layer_group_name
from server.utils import get_current_request

logger = get_logger(__name__)


@database_sync_to_async  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def get_user_pk(username: Any) -> Any:
    return get_active_user_pk_by_username(username)


@database_sync_to_async  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def get_can_push_message(pk: Any) -> Any:
    return UserConfig(pk).PUSH_CHAT_MESSAGE


async def notify_at_user_msg(data: dict[str, Any], username: str) -> None:
    text = data.get("text") or ""
    if text.startswith("@"):
        target = text.split(" ")[0].split("@")
        if len(target) > 1:
            target = target[1]
            try:
                pk = await get_user_pk(target)
                if pk and await get_can_push_message(pk):
                    push_message = {
                        "title": f"用户 {username} 发来一条消息",
                        "message": text,
                        "level": "info",
                        "notice_type": {"label": "聊天室", "value": 0},
                        "message_type": "chat_message",
                    }
                    await async_push_message(pk, push_message)
            except Exception as e:
                logger.error(e)


@database_sync_to_async  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def websocket_login_success(user_obj: Any, channel_name: Any) -> None:
    request = get_current_request()
    request.channel_name = channel_name
    login_success(request, user_obj, UserLoginLog.LoginTypeChoices.WEBSOCKET)
    # 会话登记（UserSession）：WS 会话在线判定走 channel 存活，登记供在线列表
    # 统一数据源与会话管理使用；失败仅告警不影响 WS 接入
    try:
        register_user_session(request, user_obj, UserLoginLog.LoginTypeChoices.WEBSOCKET, channel_name=channel_name)
    except Exception:  # noqa: BLE001 会话管理属附加能力
        logger.warning("register websocket session failed", exc_info=True)


@database_sync_to_async  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def websocket_logout_success(channel_name: Any) -> None:
    websocket_session_logout(channel_name)


class MessageNotify(AsyncJsonWebsocket):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(args, kwargs)
        self.group_name = ""
        self.disconnected = True
        self.user: Any = None
        self.ws_session_registered = False

    async def connect(self) -> None:
        self.user = self.scope["user"]
        if not self.user:
            logger.error(f"user not exists. so close. {self.scope}")
            await asyncio.sleep(3)
            # https://developer.mozilla.org/zh-CN/docs/Web/API/CloseEvent#status_codes
            await self.close(4401)
        else:
            logger.info(f"{self.user} connect success")
            group_name = self.scope["url_route"]["kwargs"].get("group_name")
            username = self.scope["url_route"]["kwargs"].get("username")
            if username and group_name and username != self.user.username:  # 加入聊天室房间
                self.group_name = "message_system_default_0"
            else:  # 加入个人消息推送组
                self.group_name = get_user_layer_group_name(self.user.pk)
                await websocket_login_success(self.user, self.channel_name)
                self.ws_session_registered = True

            self.disconnected = False
            # Join room group
            await self.channel_layer.group_add(self.group_name, self.channel_name)
            await self.accept()

    async def disconnect(self, close_code: Any) -> None:
        self.disconnected = True
        if self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        if self.ws_session_registered:
            # 优雅断开：按 channel 置会话离线（异常残留由保留期清理兜底）
            try:
                await websocket_logout_success(self.channel_name)
            except Exception:  # noqa: BLE001
                logger.warning("websocket session logout failed", exc_info=True)

        logger.info(f"{self.user} disconnect")

    # Receive message from WebSocket
    async def receive_json(self, action: Any, data: Any, content: Any, **kwargs: Any) -> None:
        match action:
            case "chat_message":
                # 历史通道（ws/message）的聊天广播：**无落库、无内容校验、无权限校验**，
                # 且可向共享组注入任意帧（连接握手允许落到 message_system_default_0）。
                # 前端已切换到 ws/chat（落库 + 校验 + 权限 + 限流），本路径默认关闭
                # （fail-closed）：仅在确需兼容老客户端时显式开启
                # CHAT_LEGACY_WS_BROADCAST_ENABLED: true。
                if not settings.CHAT_LEGACY_WS_BROADCAST_ENABLED:
                    logger.warning(
                        "legacy ws/message chat_message rejected (disabled; use ws/chat). user=%s", self.user.pk
                    )
                    await self.close()
                    return
                data["pk"] = self.user.pk
                data["username"] = self.user.username
                # Send message to room group
                await self.channel_layer.group_send(self.group_name, {"type": "chat_message", "data": data})
                await notify_at_user_msg(data, self.user.username)

            case _:
                logger.error(f"action unknown. so close. {content}")
                await asyncio.sleep(3)
                await self.close()

    # 下面查看文件方法忽略
    async def task_log(self, event: Any) -> None:
        task_id = event.get("data", {}).get("task_id")
        log_path = get_celery_task_log_path(task_id)
        await self.async_handle_task(task_id, log_path)

    async def async_handle_task(self, task_id: Any, log_path: Any) -> None:
        logger.info(f"Task id: {task_id}")
        while not self.disconnected:
            if not os.path.exists(log_path):
                await self.send_json({"message": ".", "task": task_id})
                await asyncio.sleep(0.5)
            else:
                await self.send_task_log(task_id, log_path)
                break

    async def send_task_log(self, task_id: Any, log_path: Any) -> None:
        await self.send_json({"message": "\r\n"})
        try:
            logger.debug(f"Task log path: {log_path}")
            async with aiofiles.open(log_path, "rb") as task_log_f:
                await task_log_f.seek(0, os.SEEK_END)
                backup = min(4096 * 5, await task_log_f.tell())
                await task_log_f.seek(-backup, os.SEEK_END)
                while not self.disconnected:
                    data = await task_log_f.read(4096)
                    if data:
                        data = data.replace(b"\n", b"\r\n")
                        await self.send_json({"message": data.decode(errors="ignore"), "task": task_id})
                    await asyncio.sleep(0.2)
        except OSError as e:
            logger.warning(f"Task log path open failed: {e}")
