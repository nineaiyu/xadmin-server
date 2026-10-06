#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : utils
# author : ly_13
# date : 3/6/2024
import asyncio
import uuid

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from common.cache.storage import WebSocketMsgResultCache

channel_layer = get_channel_layer()

# 在线信息快照缓存时长（秒）。在线列表接口与推送共用同一份快照，
# 避免多端同时刷新/推送时重复做在线统计。
ONLINE_INFO_CACHE_TTL = 5
ONLINE_INFO_CACHE_KEY = "online_info_snapshot"
# 在线 channel 明细快照（user_pk -> [channel]）：用户列表「在线数」等展示字段与非
# 实时动作共用；强制下线等需要实时明细的动作走 use_snapshot=False 直查。
ONLINE_LAYERS_CACHE_KEY = "online_layers_snapshot"


def parse_online_user_pk(group):
    """从个人消息推送组名中解析用户 pk，非法组名返回 None（不再混入 pk=0）。"""
    prefix = f"{settings.CACHE_KEY_TEMPLATE.get('websocket_group_key')}_"
    if group and group.startswith(prefix):
        tail = group[len(prefix) :]
        if tail.isdigit():
            return int(tail)
    return None


@async_to_sync
async def get_online_info():
    """在线用户与 channel 列表。

    优先走反向索引 online:users（一条 ZRANGEBYSCORE）+ 批量 pipeline 取
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


# 聊天室通道分组（channel layer 命名空间，非 cache 键）：
# - 公共聊天室广播组：全站单例；
# - 用户聊天组：私聊/AI 消息与未读红点定向推送（多端同步）。
# 命名刻意避开 websocket_group_ 前缀：在线索引只认个人推送组，聊天连接不参与在线计数。
CHAT_PUBLIC_GROUP = "chat_room_public"
CHAT_USER_GROUP_PREFIX = "chat_user"


def get_public_chat_group_name() -> str:
    return CHAT_PUBLIC_GROUP


def get_chat_user_group_name(user_pk) -> str:
    return f"{CHAT_USER_GROUP_PREFIX}_{user_pk}"


async def async_push_chat_message(room_pks, payload: dict, message_type="chat_message"):
    """把聊天帧推给若干用户的聊天连接（多端同步；公共房间由调用方走公共组）。"""
    for user_pk in dict.fromkeys(room_pks):
        await channel_layer.group_send(get_chat_user_group_name(user_pk), {"type": message_type, "data": payload})


def room_event_groups(room) -> list[str]:
    """房间事件目标组（聊天室拓扑的唯一口径，同步 DB 查询）。

    - 公共聊天室 → 公共广播组（全员在线连接）；
    - 私聊 / AI → 成员各自的聊天组（多端同步）。
    """
    from message.models import ChatRoom, ChatRoomMember

    if room.room_type == ChatRoom.RoomType.PUBLIC:
        return [CHAT_PUBLIC_GROUP]
    return [
        get_chat_user_group_name(user_pk)
        for user_pk in ChatRoomMember.objects.filter(room=room).values_list("user_id", flat=True)
    ]


@async_to_sync
async def _group_broadcast(groups, payload: dict, message_type: str):
    for group in dict.fromkeys(groups):
        await channel_layer.group_send(group, {"type": message_type, "data": payload})


def push_room_event(room, payload: dict, message_type="chat_message"):
    """REST 侧同步广播入口（撤回 / AI 回复）。

    先在同步上下文解析目标组，再做一次异步投递：不能把 DB 查询放进
    `async_to_sync` 包裹的协程里（Django 会抛 SynchronousOnlyOperation）。
    """
    _group_broadcast(room_event_groups(room), payload, message_type)


def broadcast_message_recall(message, operator_pk) -> dict:
    """向房间广播撤回事件（双方/多端同步），返回广播载荷（REST 响应 data 复用）。

    撤回事件的载荷形状是 WS/REST 共同契约；房间不存在（已解散）只落静默。
    """
    from message.models import ChatRoom

    room = ChatRoom.objects.filter(pk=message.room_id).first()
    payload = {
        "message_id": message.pk,
        "id": message.pk,
        "room_id": message.room_id,
        "operator_pk": operator_pk,
    }
    if room is not None:
        push_room_event(room, payload, message_type="chat_recall")
    return payload


async def async_push_message(user_pk: str | int, message: dict, message_type="push_message"):
    await channel_layer.group_send(get_user_layer_group_name(user_pk), {"type": message_type, "data": message})


async def async_push_messages(user_pks, message: dict, message_type="push_message"):
    """批量推送。整批收进一个 async 函数，只做一次同步桥接；
    message 仅序列化一次，不再对每个用户做 json.loads(json.dumps(...)) 深拷贝。"""
    for user_pk in dict.fromkeys(user_pks):
        await async_push_message(user_pk, message, message_type)


@async_to_sync
async def push_messages(user_pks, message: dict, message_type="push_message"):
    await async_push_messages(user_pks, message, message_type)


async def get_layers_form_group(group):
    return await channel_layer.get_layers(group)


async def layers_for_groups(groups):
    """批量取多组 channel（不支持批量接口的实现回退逐组）。

    get_layers_for_groups 按节点归并 pipeline，单 Redis 部署下整个请求一次往返。
    """
    if hasattr(channel_layer, "get_layers_for_groups"):
        return await channel_layer.get_layers_for_groups(groups)
    return {group: await get_layers_form_group(group) for group in groups}


async def query_online_users_layers(pks):
    """实时查询多个用户的在线 channel layers（一次同步桥接完成全部查询）。"""
    groups = [get_user_layer_group_name(user_pk) for user_pk in pks]
    layers = await layers_for_groups(groups)
    return {user_pk: layers.get(group, []) for user_pk, group in zip(pks, groups, strict=True)}


@async_to_sync
async def build_online_layers_snapshot():
    """全量在线用户的 channel 明细快照（user_pk -> [channel]）。

    与在线列表页的快照（``get_online_info``）同源：先走反向索引 ``online:users``
    一条 ZRANGEBYSCORE 取在线 pk，再一次批量 pipeline 取各组 channel；反向索引为空
    （Redis 重启后首个心跳尚未到来的窗口）时回退 ``get_groups`` 重建。结果整体缓存
    （5s TTL），把「每个列表请求各查一次 ZSET 管道」摊薄为 5 秒一次。
    """
    pks = []
    if hasattr(channel_layer, "get_online_user_pks"):
        pks = await channel_layer.get_online_user_pks()
    groups = [get_user_layer_group_name(pk) for pk in pks]
    if not groups:
        groups = await channel_layer.get_groups()
        pks = sorted(pk for pk in (parse_online_user_pk(g) for g in groups) if pk is not None)
    if not groups:
        return {}
    by_group = await layers_for_groups(groups)
    return {pk: by_group.get(group, []) for pk, group in zip(pks, groups, strict=True)}


def get_online_users_layers(user_pks, *, use_snapshot=True):
    """批量获取多个用户的在线 channel layers，user_pk 自动去重。

    - ``use_snapshot=True``（默认，展示口径）：优先读 5s 快照（与在线列表页/聊天在线态
      同一份心跳口径），未命中时重建快照。用户列表「在线数」、登录日志「在线态」这类
      展示字段不需要心跳级实时性，快照把每请求一次的 ZSET 管道查询摊薄到 5 秒一次；
    - ``use_snapshot=False``（动作口径）：强制实时查询——强制下线/登出踢连接必须拿到
      当前真实 channel 列表，不能吃快照延迟。
    """
    pks = list(dict.fromkeys(user_pks))
    if not pks:
        return {}
    if not use_snapshot:
        return async_to_sync(query_online_users_layers)(pks)
    snapshot = cache.get(ONLINE_LAYERS_CACHE_KEY)
    if snapshot is None:
        snapshot = build_online_layers_snapshot()
        cache.set(ONLINE_LAYERS_CACHE_KEY, snapshot, ONLINE_INFO_CACHE_TTL)
    return {user_pk: snapshot.get(user_pk, []) for user_pk in pks}


@async_to_sync
async def get_online_users():
    """在线用户 pk 列表（反向索引一条命令，SCAN 仅作降级路径）"""
    if hasattr(channel_layer, "get_online_user_pks"):
        online_user_pks = await channel_layer.get_online_user_pks()
        if online_user_pks:
            return online_user_pks
    return [pk for pk in (parse_online_user_pk(g) for g in await channel_layer.get_groups()) if pk is not None]


async def async_push_layer_message(channel_name: str, message: dict, message_type="push_message"):
    await channel_layer.send(channel_name, {"type": message_type, "data": message})


@async_to_sync
async def send_logout_msg(user_pk: str | int, channel_names: list[str] | None = None):
    group_name = get_user_layer_group_name(user_pk)
    if not channel_names:
        channel_names = await get_layers_form_group(group_name)
    if channel_names:
        for channel_name in channel_names:
            await async_push_layer_message(channel_name, {"message_type": "logout"})
            await channel_layer.group_discard(group_name, channel_name)


@async_to_sync
async def batch_send_logout_msg(layers_by_user: dict):
    """批量向多个用户的在线 channel 推送 logout 并退组。

    入参为 ``get_online_users_layers`` 的返回（user_pk -> [channel]）；逐 channel
    send + group_discard 与 ``send_logout_msg`` 同语义，仅把逐用户各一次的同步
    桥接合并为整批一次（批量踢线的桥接开销从 O(用户数) 降到 O(1)）。
    """
    for user_pk, channel_names in layers_by_user.items():
        if not channel_names:
            continue
        group_name = get_user_layer_group_name(user_pk)
        for channel_name in channel_names:
            await async_push_layer_message(channel_name, {"message_type": "logout"})
            await channel_layer.group_discard(group_name, channel_name)


@async_to_sync
async def push_message(user_pk: str | int, message: dict, message_type="push_message"):
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
async def push_message_and_wait_result(
    channel_name: str, message: dict, message_type="push_message", mid=None, timeout=5
):
    """
    客户端返回结果必须和发送的mid一致，否则拿不到数据
    """
    if mid is None:
        mid = uuid.uuid4().hex
    await channel_layer.send(channel_name, {"type": message_type, "data": message, "mid": mid})
    try:
        return await asyncio.wait_for(wait_for_mid_result(mid), timeout=timeout)
    except TimeoutError:
        raise TimeoutError(_("Wait for result timeout")) from None
