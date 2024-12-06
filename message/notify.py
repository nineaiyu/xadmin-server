#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : chat
# author : ly_13
# date : 6/2/2023
import asyncio
import datetime
import json
import os
import time

import aiofiles
from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from rest_framework.utils import encoders

from common.celery.utils import get_celery_task_log_path
from common.core.config import UserConfig
from common.decorators import cached_method
from common.utils import get_logger
from common.utils.timezone import local_now_display
from message.utils import async_push_message, online_caches
from system.models import UserInfo
from system.serializers.userinfo import UserInfoSerializer

logger = get_logger(__name__)


@database_sync_to_async
@cached_method()
def get_userinfo(user):
    result = UserInfoSerializer(instance=user).data
    return result


@database_sync_to_async
def get_user_pk(username):
    try:
        return UserInfo.objects.filter(username=username, is_active=True).values_list('pk', flat=True).first()
    except UserInfo.DoesNotExist:
        return


@sync_to_async
def get_can_push_message(pk):
    return UserConfig(pk).PUSH_CHAT_MESSAGE


class MessageNotify(AsyncJsonWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(args, kwargs)
        self.room_group_name = None
        self.disconnected = True
        self.user = None

    async def connect(self):
        self.user = self.scope["user"]
        if not self.user:
            # https://developer.mozilla.org/zh-CN/docs/Web/API/CloseEvent#status_codes
            await self.close(4401)
        else:
            logger.info(f"{self.user} connect success")
            group_name = self.scope["url_route"]["kwargs"].get('group_name')
            username = self.scope["url_route"]["kwargs"].get('username')
            # # data = verify_token(token, room_name, success_once=True)
            if username and group_name and username != self.user.username:
                self.disconnected = False
                self.room_group_name = "message_system_default"
                # self.room_group_name = f"message_{room_name}"
                # Join room group
                await self.channel_layer.group_add(self.room_group_name, self.channel_name)
                await self.accept()
            else:
                #     logger.error(f"room_name:{room_name} token:{username} auth failed")
                #     await self.close()
                self.room_group_name = f"{settings.CACHE_KEY_TEMPLATE.get('user_websocket_key')}_{self.user.pk}"
                self.disconnected = False
                # Join room group
                await self.channel_layer.group_add(self.room_group_name, self.channel_name)
                online_caches.push(self.room_group_name, {'time': local_now_display(), 'name': self.channel_name})

                await self.accept()
                # 建立连接，推送用户信息
                # await self.userinfo(None)

    async def disconnect(self, close_code):
        self.disconnected = True
        if self.room_group_name:
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

            if self.channel_layer._get_group_channel_name(self.room_group_name) not in self.channel_layer.groups:
                online_caches.pop(self.room_group_name)

        logger.info(f"{self.user} disconnect")

    @classmethod
    async def encode_json(cls, content):
        return json.dumps(content, cls=encoders.JSONEncoder, ensure_ascii=False)

    # Receive message from WebSocket
    async def receive_json(self, content, **kwargs):
        action = content.get('action')
        if not action or action not in ['userinfo', 'push_message', 'chat_message']:
            await self.close()
        data = content.get('data', {})
        if action == "chat_message":
            data['pk'] = self.user.pk
            data['username'] = self.user.username
            # Send message to room group
            await self.channel_layer.group_send(
                self.room_group_name, {"type": "chat_message", "data": data}
            )
            text = data.get('text')
            if text.startswith('@'):
                target = text.split(' ')[0].split('@')
                if len(target) > 1:
                    target = target[1]
                    try:
                        pk = await get_user_pk(target)
                        if pk and await(get_can_push_message(pk)):
                            push_message = {
                                'title': f"用户 {self.user.username} 发来一条消息",
                                'message': text,
                                'level': 'info',
                                'notice_type': {'label': '聊天室', 'value': 0},
                                'message_type': 'chat_message',
                            }
                            await async_push_message(pk, push_message)
                    except Exception as e:
                        logger.error(e)
        else:
            await self.channel_layer.send(self.channel_name, {"type": action, "data": data})

    # rec: {"action":"userinfo","data":{}}
    async def userinfo(self, event):
        data = {
            'userinfo': await get_userinfo(self.user),
            'pk': self.user.pk
        }
        await self.send_data('userinfo', {'data': data})

    # 系统推送消息到客户端，推送消息格式如下：{"time": 1709714533.5625794, "action": "push_message", "data": {"message": 11}}
    async def push_message(self, event):
        data = event["data"]
        await self.send_data('push_message', {'data': data})

    # 客户端聊天消息，已经失效
    async def chat_message(self, event):
        data = event["data"]
        data['time'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
        # Send message to WebSocket
        await self.send_data('chat_message', {'data': data})

    async def send_data(self, action, content, close=False):
        data = {
            'time': time.time(),
            'action': action
        }
        data.update(content)
        return await super().send_json(data, close)

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
