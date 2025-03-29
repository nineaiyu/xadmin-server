#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : base
# author : ly_13
# date : 3/27/2025
import datetime
import json
from typing import Dict

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from rest_framework.utils import encoders

from common.decorators import cached_method
from system.serializers.userinfo import UserInfoSerializer


@database_sync_to_async
@cached_method()
def get_userinfo(user):
    result = UserInfoSerializer(instance=user).data
    return result


class AsyncJsonWebsocket(AsyncJsonWebsocketConsumer):
    user: None

    @classmethod
    async def encode_json(cls, content):
        return json.dumps(content, cls=encoders.JSONEncoder, ensure_ascii=False)

    async def send_base_json(self, action: str, data: Dict | str | bytes | None, status: bool = True, close=False):
        content = {
            "action": action,
            "timestamp": str(datetime.datetime.now()),
            "data": data,
            "status": "success" if status else "failed",
        }
        await self.send_json(content, close)

    async def userinfo(self, event):
        await self.send_base_json(event["type"], await get_userinfo(self.user))

    # 系统推送消息到客户端，推送消息格式如下：{"timestamp": 1709714533.5625794, "action": "push_message", "data": {"message_type": 11}}
    async def push_message(self, event):
        await self.send_base_json(event["type"], event["data"])

    async def chat_message(self, event):
        await self.send_base_json(event["type"], event["data"])
