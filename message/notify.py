#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : chat
# author : ly_13
# date : 6/2/2023
import asyncio
import datetime
import logging
import os
import time

import aiofiles
from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.utils.module_loading import import_string
from rest_framework_simplejwt.exceptions import TokenError

from common.celery.utils import get_celery_task_log_path

logger = logging.getLogger(__name__)


async def token_auth(scope):
    cookies = scope.get('cookies')
    if cookies:
        token = f"{cookies.get('X-Token')}".encode('utf-8')
        if token:
            try:
                auth = import_string(settings.SIMPLE_JWT.get('AUTH_TOKEN_CLASSES')[0])
                auth_class = import_string(settings.REST_FRAMEWORK.get('DEFAULT_AUTHENTICATION_CLASSES')[0])()
                validated_token = auth(token)
                return True, await sync_to_async(auth_class.get_user)(validated_token)
            except TokenError as e:
                return False, e.args[0]
    return False, False


class MessageNotify(AsyncJsonWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(args, kwargs)
        self.room_group_name = None
        self.disconnected = True
        self.username = ""

    async def connect(self):
        status, user_obj = await token_auth(self.scope)
        if not status:
            logger.error(f"auth failed {user_obj}")
            # https://developer.mozilla.org/zh-CN/docs/Web/API/CloseEvent#status_codes
            await self.close(4401)
        else:
            logger.info(f"{user_obj} connect success")
            room_name = self.scope["url_route"]["kwargs"].get('room_name')
            username = self.scope["url_route"]["kwargs"].get('username')
            # data = verify_token(token, room_name, success_once=True)
            if username and room_name:
                self.disconnected = False
                self.username = username
                self.room_group_name = f"message_{room_name}"
                # Join room group
                await self.channel_layer.group_add(self.room_group_name, self.channel_name)
                await self.accept()
            else:
                logger.error(f"room_name:{room_name} token:{username} auth failed")
                await self.close()

    async def disconnect(self, close_code):
        self.disconnected = True
        if self.room_group_name:
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    # Receive message from WebSocket
    async def receive_json(self, content, **kwargs):
        action = content.get('action')
        if not action:
            await self.close()
        data = content.get('data', {})
        if action == "message":
            data['uid'] = self.channel_name
            data['username'] = self.username
            # Send message to room group
            await self.channel_layer.group_send(
                self.room_group_name, {"type": "chat_message", "data": data}
            )
        else:
            await self.channel_layer.send(self.channel_name, {"type": action, "data": data})

    async def userinfo(self, event):
        data = {
            'username': self.username,
            'uid': self.channel_name
        }
        await self.send_data('userinfo', {'data': data})

    async def chat_message(self, event):
        data = event["data"]
        data['time'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
        # Send message to WebSocket
        await self.send_data('message', {'data': data})

    async def send_data(self, action, content, close=False):
        data = {
            'time': time.time(),
            'action': action
        }
        data.update(content)
        return await super().send_json(data, close)

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
