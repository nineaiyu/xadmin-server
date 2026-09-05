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
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from common.cache.storage import WebSocketMsgResultCache

channel_layer = get_channel_layer()

# PERF-08：在线信息快照缓存时长（秒）。在线列表接口与推送共用同一份快照，
# 避免多端同时刷新/推送时重复做在线统计。
ONLINE_INFO_CACHE_TTL = 5
ONLINE_INFO_CACHE_KEY = "online_info_snapshot"


def parse_online_user_pk(group):
    """从个人消息推送组名中解析用户 pk，非法组名返回 None（不再混入 pk=0）。"""
    prefix = f"{settings.CACHE_KEY_TEMPLATE.get('websocket_group_key')}_"
    if group and group.startswith(prefix):
        tail = group[len(prefix):]
        if tail.isdigit():
            return int(tail)
    return None


@async_to_sync
async def get_online_info():
    """在线用户与 channel 列表。

    PERF-08：优先走反向索引 online:users（一条 ZRANGEBYSCORE）+ 批量 pipeline 取
    各组 channel，不再 SCAN 全库 + 逐 group 串行往返；结果整体缓存为快照。
    反向索引为空时（Redis 重启后首个心跳尚未到来的窗口）回退到 get_groups 重建。
    """
    snapshot = cache.get(ONLINE_INFO_CACHE_KEY)
    if snapshot is not None:
        return snapshot

    online_user_pks = []
    groups = []
    if hasattr(channel_layer, "get_online_user_pks"):
        online_user_pks = await channel_layer.get_online_user_pks()
        groups = [get_user_layer_group_name(pk) for pk in online_user_pks]
    if not groups:
        groups = await channel_layer.get_groups()
        online_user_pks = [pk for pk in (parse_online_user_pk(g) for g in groups) if pk is not None]
        online_user_pks.sort()

    if hasattr(channel_layer, "get_layers_for_groups"):
        by_group = await channel_layer.get_layers_for_groups(groups)
    else:
        by_group = {group: await get_layers_form_group(group) for group in groups}
    online_user_sockets = [channel for group in groups for channel in by_group.get(group, [])]

    result = (online_user_pks, online_user_sockets)
    cache.set(ONLINE_INFO_CACHE_KEY, result, ONLINE_INFO_CACHE_TTL)
    return result


def get_user_layer_group_name(user_pk):
    return f"{settings.CACHE_KEY_TEMPLATE.get('websocket_group_key')}_{user_pk}"


async def async_push_message(user_pk: str | int, message: Dict, message_type='push_message'):
    await channel_layer.group_send(get_user_layer_group_name(user_pk), {'type': message_type, 'data': message})


async def async_push_messages(user_pks, message: Dict, message_type='push_message'):
    """PERF-08：批量推送。整批收进一个 async 函数，只做一次同步桥接；
    message 仅序列化一次，不再对每个用户做 json.loads(json.dumps(...)) 深拷贝。"""
    for user_pk in dict.fromkeys(user_pks):
        await async_push_message(user_pk, message, message_type)


def push_messages(user_pks, message: Dict, message_type='push_message'):
    return async_push_messages(user_pks, message, message_type)


async def get_layers_form_group(group):
    return await channel_layer.get_layers(group)


@async_to_sync
async def get_online_users_layers(user_pks):
    """批量获取多个用户的在线 channel layers，一次同步桥接完成全部查询，user_pk 自动去重"""
    result = {}
    for user_pk in dict.fromkeys(user_pks):
        result[user_pk] = await get_layers_form_group(get_user_layer_group_name(user_pk))
    return result


@async_to_sync
async def get_online_users():
    """在线用户 pk 列表（PERF-08：反向索引一条命令，SCAN 仅作降级路径）"""
    if hasattr(channel_layer, "get_online_user_pks"):
        online_user_pks = await channel_layer.get_online_user_pks()
        if online_user_pks:
            return online_user_pks
    return [pk for pk in (parse_online_user_pk(g) for g in await channel_layer.get_groups()) if pk is not None]


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
