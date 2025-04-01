#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : base
# author : ly_13
# date : 3/27/2025
import asyncio
import datetime
import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils.translation import gettext_lazy as _
from rest_framework.utils import encoders

from common.decorators import cached_method
from common.utils import get_logger
from message.utils import set_mid_result_to_cache
from system.serializers.userinfo import UserInfoSerializer

logger = get_logger(__name__)


@database_sync_to_async
@cached_method()
def get_userinfo(user):
    result = UserInfoSerializer(instance=user).data
    return result


class AsyncJsonWebsocket(AsyncWebsocketConsumer):
    user: None
    group_name: str

    @classmethod
    async def encode_json(cls, content):
        return json.dumps(content, cls=encoders.JSONEncoder, ensure_ascii=False)

    async def send_json(self, content, close=False):
        """
        Encode the given content as JSON and send it to the client.
        """
        await super().send(text_data=await self.encode_json(content), close=close)

    @classmethod
    async def decode_json(cls, text_data):
        return json.loads(text_data)

    async def receive_json(self, action, data, content, **kwargs):
        """
        数据格式如下：
        {
            "action": "chat_message",
            "data": ""
        }
        """
        pass

    async def receive_bytes(self, bytes_data, **kwargs):

        pass

    async def send_base_json(self, action: str, data=None, mid=None, code=1000, detail=None, close=False, **kwargs):
        """
        action: 动作
        data: 数据
        mid: 消息ID，该ID和发送端的mid保持一致
        """
        content = {
            'code': code,
            'action': action,
            'detail': detail if detail else (_("Operation successful") if code == 1000 else _("Operation failed")),
            'timestamp': str(datetime.datetime.now()),
        }
        if data:
            content['data'] = data
        if mid:
            content['mid'] = mid
        content.update(kwargs)
        await self.send_json(content, close)

    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        if text_data:
            try:
                content = await self.decode_json(text_data)
            except Exception as e:
                logger.error("failed to decode json", exc_info=e)
                return
            action = content.get('action')
            if not action:
                logger.error(f"action not exists. so close. {content}")
                await asyncio.sleep(3)
                await self.close()
            if mid := content.get('mid'):
                set_mid_result_to_cache(mid, content)
            data = content.get('data', {})
            match action:
                case 'ping' | 'userinfo' | 'push_message':
                    await self.channel_layer.send(self.channel_name, {"type": action, "data": data})
                case _:
                    await self.receive_json(action, data, content, **kwargs)
            return
        if bytes_data:
            return await self.receive_bytes(bytes_data, **kwargs)

        raise ValueError("No text section for incoming WebSocket frame!")

    async def _send_base(self, event):
        data = event['data']
        if isinstance(data, str):
            await self.send_base_json(event["type"], data, mid=event.get("mid"))
        else:
            await self.send_base_json(data.get("action", event["type"]), data, mid=data.get("mid", event.get("mid")))

    async def ping(self, event):
        await self.channel_layer.update_active_layers(self.group_name, self.channel_name)
        event['data'] = 'pong'
        await self._send_base(event)

    async def userinfo(self, event):
        event['data'] = await get_userinfo(self.user)
        await self._send_base(event)

    # 系统推送消息到客户端，推送消息格式如下：{"timestamp": 1709714533.5625794, "action": "push_message", "data": {"message_type": 11}}
    async def push_message(self, event):
        await self._send_base(event)

    async def chat_message(self, event):
        await self._send_base(event)
