#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : chat
# author : ly_13
# date : 6/2/2023
import asyncio
import datetime
import json
import logging
import os
import time

import aiofiles
from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.utils.module_loading import import_string
from rest_framework.utils import encoders
from rest_framework_simplejwt.exceptions import TokenError

from common.base.magic import MagicCacheData
from common.celery.utils import get_celery_task_log_path
from common.core.config import UserConfig
from message.utils import async_push_message
from system.models import UserInfo
from system.serializers.userinfo import UserInfoSerializer

logger = logging.getLogger(__name__)


@sync_to_async
@MagicCacheData.make_cache(timeout=5, key_func=lambda x: x.pk)
def get_userinfo(user_obj):
    return UserInfoSerializer(instance=user_obj).data


@sync_to_async
def get_user_pk(username):
    try:
        return UserInfo.objects.filter(username=username, is_active=True).values_list('pk', flat=True).first()
    except UserInfo.DoesNotExist:
        return


@sync_to_async
def token_auth(scope):
    cookies = scope.get('cookies')
    if cookies:
        token = f"{cookies.get('X-Token')}".encode('utf-8')
        if token:
            try:
                auth = import_string(settings.SIMPLE_JWT.get('AUTH_TOKEN_CLASSES')[0])
                auth_class = import_string(settings.REST_FRAMEWORK.get('DEFAULT_AUTHENTICATION_CLASSES')[0])()
                validated_token = auth(token)
                return True, auth_class.get_user(validated_token)
            except TokenError as e:
                return False, e.args[0]
    return False, False


@sync_to_async
def get_can_push_message(pk):
    return UserConfig(pk).PUSH_CHAT_MESSAGE


class MessageNotify(AsyncJsonWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(args, kwargs)
        self.room_group_name = None
        self.disconnected = True
        self.user_obj = None

    async def connect(self):
        status, self.user_obj = await token_auth(self.scope)
        if not status:
            logger.error(f"auth failed {self.user_obj}")
            # https://developer.mozilla.org/zh-CN/docs/Web/API/CloseEvent#status_codes
            await self.close(4401)
        else:
            logger.info(f"{self.user_obj} connect success")
            group_name = self.scope["url_route"]["kwargs"].get('group_name')
            username = self.scope["url_route"]["kwargs"].get('username')
            # # data = verify_token(token, room_name, success_once=True)
            if username and group_name and username != self.user_obj.username:
                self.disconnected = False
                self.room_group_name = "message_system_default"
                # self.room_group_name = f"message_{room_name}"
                # Join room group
                await self.channel_layer.group_add(self.room_group_name, self.channel_name)
                await self.accept()
            else:
                #     logger.error(f"room_name:{room_name} token:{username} auth failed")
                #     await self.close()
                self.room_group_name = f"{settings.CACHE_KEY_TEMPLATE.get('user_websocket_key')}_{self.user_obj.pk}"
                self.disconnected = False
                # Join room group
                await self.channel_layer.group_add(self.room_group_name, self.channel_name)
                await self.accept()
                # 建立连接，推送用户信息
                # await self.userinfo(None)

    async def disconnect(self, close_code):
        self.disconnected = True
        if self.room_group_name:
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
        logger.info(f"{self.user_obj} disconnect")

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
            data['pk'] = self.user_obj.pk
            data['username'] = self.user_obj.username
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
                                'title': f"用户 {self.user_obj.username} 发来一条消息",
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
            'userinfo': await get_userinfo(self.user_obj),
            'pk': self.user_obj.pk
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
