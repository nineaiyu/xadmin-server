#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : chat
# author : ly_13
# date : 6/2/2023
import asyncio
import os
from typing import Dict

import aiofiles
from channels.db import database_sync_to_async

from common.celery.utils import get_celery_task_log_path
from common.core.config import UserConfig
from common.utils import get_logger
from message.base import AsyncJsonWebsocket
from message.utils import async_push_message, get_user_layer_group_name
from server.utils import get_current_request
from system.models import UserInfo, UserLoginLog
from system.views.auth.login import login_success

logger = get_logger(__name__)


@database_sync_to_async
def get_user_pk(username):
    try:
        return UserInfo.objects.filter(username=username, is_active=True).values_list('pk', flat=True).first()
    except UserInfo.DoesNotExist:
        return


@database_sync_to_async
def get_can_push_message(pk):
    return UserConfig(pk).PUSH_CHAT_MESSAGE


async def notify_at_user_msg(data: Dict, username: str):
    text = data.get('text')
    if text.startswith('@'):
        target = text.split(' ')[0].split('@')
        if len(target) > 1:
            target = target[1]
            try:
                pk = await get_user_pk(target)
                if pk and await(get_can_push_message(pk)):
                    push_message = {
                        'title': f"用户 {username} 发来一条消息",
                        'message': text,
                        'level': 'info',
                        'notice_type': {'label': '聊天室', 'value': 0},
                        'message_type': 'chat_message',
                    }
                    await async_push_message(pk, push_message)
            except Exception as e:
                logger.error(e)


@database_sync_to_async
def websocket_login_success(user_obj, channel_name):
    request = get_current_request()
    request.channel_name = channel_name
    login_success(request, user_obj, UserLoginLog.LoginTypeChoices.WEBSOCKET)


class MessageNotify(AsyncJsonWebsocket):

    def __init__(self, *args, **kwargs):
        super().__init__(args, kwargs)
        self.group_name = ''
        self.disconnected = True
        self.user = None

    async def connect(self):
        self.user = self.scope["user"]
        if not self.user:
            logger.error(f"user not exists. so close. {self.scope}")
            await asyncio.sleep(3)
            # https://developer.mozilla.org/zh-CN/docs/Web/API/CloseEvent#status_codes
            await self.close(4401)
        else:
            logger.info(f"{self.user} connect success")
            group_name = self.scope["url_route"]["kwargs"].get('group_name')
            username = self.scope["url_route"]["kwargs"].get('username')
            if username and group_name and username != self.user.username:  # 加入聊天室房间
                self.group_name = 'message_system_default_0'
            else:  # 加入个人消息推送组
                self.group_name = get_user_layer_group_name(self.user.pk)
                await websocket_login_success(self.user, self.channel_name)

            self.disconnected = False
            # Join room group
            await self.channel_layer.group_add(self.group_name, self.channel_name)
            await self.accept()

    async def disconnect(self, close_code):
        self.disconnected = True
        if self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

        logger.info(f"{self.user} disconnect")

    # Receive message from WebSocket
    async def receive_json(self, action, data, content, **kwargs):
        match action:
            case 'chat_message':
                data['pk'] = self.user.pk
                data['username'] = self.user.username
                # Send message to room group
                await self.channel_layer.group_send(
                    self.group_name, {"type": "chat_message", "data": data}
                )
                await notify_at_user_msg(data, self.user.username)

            case _:
                logger.error(f"action unknown. so close. {content}")
                await asyncio.sleep(3)
                await self.close()

    # 下面查看文件方法忽略
    async def task_log(self, event):
        task_id = event.get("data", {}).get('task_id')
        log_path = get_celery_task_log_path(task_id)
        await self.async_handle_task(task_id, log_path)

    async def async_handle_task(self, task_id, log_path):
        logger.info("Task id: {}".format(task_id))
        while not self.disconnected:
            if not os.path.exists(log_path):
                await self.send_json({'message': '.', 'task': task_id})
                await asyncio.sleep(0.5)
            else:
                await self.send_task_log(task_id, log_path)
                break

    async def send_task_log(self, task_id, log_path):
        await self.send_json({'message': '\r\n'})
        try:
            logger.debug('Task log path: {}'.format(log_path))
            async with aiofiles.open(log_path, 'rb') as task_log_f:
                await task_log_f.seek(0, os.SEEK_END)
                backup = min(4096 * 5, await task_log_f.tell())
                await task_log_f.seek(-backup, os.SEEK_END)
                while not self.disconnected:
                    data = await task_log_f.read(4096)
                    if data:
                        data = data.replace(b'\n', b'\r\n')
                        await self.send_json(
                            {'message': data.decode(errors='ignore'), 'task': task_id}
                        )
                    await asyncio.sleep(0.2)
        except OSError as e:
            logger.warning('Task log path open failed: {}'.format(e))
