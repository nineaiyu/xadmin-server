#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : utils
# author : ly_13
# date : 3/6/2024
import json
from typing import Dict, List

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from rest_framework.utils import encoders

from common.cache.redis import CacheHash

online_user_cache = CacheHash(key='online_user_socket')


def get_online_user_pks():
    return {int(key.split('_')[-1]) for key in online_user_cache.get_all().keys()}


def get_user_channel_layer_group_name(user_pk):
    return f"{settings.CACHE_KEY_TEMPLATE.get('user_websocket_key')}_{user_pk}"

async def async_push_message(user_pk: str | int, message: Dict, message_type='push_message'):
    channel_layer = get_channel_layer()
    await channel_layer.group_send(get_user_channel_layer_group_name(user_pk), {
        'type': message_type,
        'data': json.dumps(message, cls=encoders.JSONEncoder, ensure_ascii=False)
    })


@async_to_sync
async def get_online_user(user_pk):
    channel_layer = get_channel_layer()
    group_channel = channel_layer._get_group_channel_name(get_user_channel_layer_group_name(user_pk))
    return list(channel_layer.groups.get(group_channel, {}))


async def async_push_layer_message(channel_name: str, message: Dict, message_type='push_message'):
    channel_layer = get_channel_layer()
    await channel_layer.send(channel_name, {'type': message_type, "data": json.dumps(message)})


@async_to_sync
async def send_logout_msg(user_pk: str | int, channel_names: List[str] = None):
    channel_layer = get_channel_layer()
    room_group_name = get_user_channel_layer_group_name(user_pk)
    if not channel_names:
        channel_names = online_user_cache.get(room_group_name)
    if channel_names:
        for channel_name in channel_names:
            await async_push_layer_message(channel_name, {"message_type": "logout"})
            await channel_layer.group_discard(room_group_name, channel_name)


@async_to_sync
async def push_message(user_pk: str | int, message: Dict, message_type='push_message'):
    return await async_push_message(user_pk, message, message_type)


@async_to_sync
async def check_message(user_obj, message):
    room_group_name = f"{settings.CACHE_KEY_TEMPLATE.get('user_websocket_key')}_{user_obj.pk}"
    channel_layer = get_channel_layer()
    group_channel = channel_layer._get_group_channel_name(room_group_name)
    group_channels = channel_layer.groups.get(group_channel, set())
    print(group_channels, group_channel)
