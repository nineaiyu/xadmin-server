#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : utils
# author : ly_13
# date : 3/6/2024
import asyncio
import uuid
from typing import Dict, List

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from common.cache.storage import WebSocketMsgResultCache

channel_layer = get_channel_layer()


@async_to_sync
async def get_online_info():
    online_user_pks = []
    online_user_sockets = []
    for group in await channel_layer.get_groups():
        online_user_pks.append(int(group.split('_')[-1]))
        online_user_sockets.extend(await get_layers_form_group(group))
    return online_user_pks, online_user_sockets


def get_user_layer_group_name(user_pk):
    return f"{settings.CACHE_KEY_TEMPLATE.get('websocket_group_key')}_{user_pk}"


async def async_push_message(user_pk: str | int, message: Dict, message_type='push_message'):
    await channel_layer.group_send(get_user_layer_group_name(user_pk), {'type': message_type, 'data': message})


async def get_layers_form_group(group):
    return await channel_layer.get_layers(group)


@async_to_sync
async def get_online_user_layers(user_pk):
    return await get_layers_form_group(get_user_layer_group_name(user_pk))


@async_to_sync
async def get_online_users():
    return [int(group.split('_')[-1]) for group in await channel_layer.get_groups()]


async def async_push_layer_message(channel_name: str, message: Dict, message_type='push_message'):
    await channel_layer.send(channel_name, {'type': message_type, "data": message})


@async_to_sync
async def send_logout_msg(user_pk: str | int, channel_names: List[str] = None):
    group_name = get_user_layer_group_name(user_pk)
    if not channel_names:
        channel_names = await get_layers_form_group(group_name)
    if channel_names:
        for channel_name in channel_names:
            await async_push_layer_message(channel_name, {"message_type": "logout"})
            await channel_layer.group_discard(group_name, channel_name)


@async_to_sync
async def push_message(user_pk: str | int, message: Dict, message_type='push_message'):
    return await async_push_message(user_pk, message, message_type)


async def wait_for_mid_result(mid):
    mid_cache = WebSocketMsgResultCache(mid)
    while True:
        if result := mid_cache.get_storage_cache():
            mid_cache.del_storage_cache()
            return result
        await asyncio.sleep(0.3)


def set_mid_result_to_cache(mid, content, timeout=10):
    WebSocketMsgResultCache(mid).set_storage_cache(content, timeout)


@async_to_sync
async def push_message_and_wait_result(channel_name: str, message: Dict, message_type='push_message', mid=None,
                                       timeout=5):
    """
    客户端返回结果必须和发送的mid一致，否则拿不到数据
    """
    if mid is None:
        mid = uuid.uuid4().hex
    await channel_layer.send(channel_name, {'type': message_type, "data": message, 'mid': mid})
    try:
        return await asyncio.wait_for(wait_for_mid_result(mid), timeout=timeout)
    except asyncio.TimeoutError:
        raise TimeoutError(_("Wait for result timeout"))
