# -*- coding: utf-8 -*-
"""测试专用 Channels 内存 channel layer。

生产环境使用 common.cache.channel.RedisChannelLayer（带 get_layers /
get_groups / get_online_user_pks / get_layers_for_groups 用于在线状态统计）。
内存版补齐同名方法，以便 message.utils 等模块在测试中可复用，并按
反向索引语义维护在线用户表。
"""

import time

from channels.layers import InMemoryChannelLayer


def _is_memory_layer(layer) -> bool:
    """InMemory 档（TestInMemoryChannelLayer）与真 Redis 层的判别面。"""
    return hasattr(layer, "_online_users")


class TestInMemoryChannelLayer(InMemoryChannelLayer):
    def __init__(self, **kwargs):
        self.layer_expire = kwargs.pop("layer_expire", 30)
        super().__init__(**kwargs)
        # 全局在线用户反向索引（user_pk -> 最后心跳时间戳）
        self._online_users = {}

    async def get_layers(self, group):
        return list(self.groups.get(group, {}).keys())

    async def get_groups(self):
        """与生产 RedisChannelLayer.get_groups 同口径：只列出个人推送组。

        旧实现返回全部 group，聊天室组（chat_room_public/chat_user_*）会被
        在线快照的降级路径混入 channels（pks 为空但 sockets 非空）。
        """
        return [group for group in self.groups.keys() if self._user_pk_from_group(group) is not None]

    @staticmethod
    def _user_pk_from_group(group):
        """与生产 RedisChannelLayer.user_pk_from_group 同口径：仅个人推送组计入在线。

        旧实现按 rsplit("_") 取尾段数字判定，聊天室等非个人组（chat_user_1）
        会被误当成在线用户混入索引，与生产语义不一致。
        """
        from django.conf import settings

        prefix = f"{settings.CACHE_KEY_TEMPLATE.get('websocket_group_key')}_"
        if group and group.startswith(prefix):
            tail = group[len(prefix) :]
            if tail.isdigit():
                return int(tail)
        return None

    async def update_active_layers(self, group, channel):
        # 与 InMemoryChannelLayer 一致：groups 为 {group: {channel: timestamp}}
        self.groups.setdefault(group, {})[channel] = time.time()
        user_pk = self._user_pk_from_group(group)
        if user_pk is not None:
            self._online_users[user_pk] = time.time()

    async def group_discard(self, group, channel):
        channels = self.groups.get(group)
        if channels is not None:
            channels.pop(channel, None)
            if not channels:
                self.groups.pop(group, None)
        user_pk = self._user_pk_from_group(group)
        if user_pk is not None and not self.groups.get(group):
            self._online_users.pop(user_pk, None)

    async def get_online_user_pks(self):
        now = time.time()
        return [pk for pk, ts in self._online_users.items() if now - ts <= self.layer_expire]

    async def get_layers_for_groups(self, groups):
        return {group: list(self.groups.get(group, {}).keys()) for group in groups}


# ---------------------------------------------------------------------------
# 双栈测试 helper：同一用例在 InMemory 档（sqlite 门禁）与真 Redis 层
# （tests/settings_real.py 档）下等价运行。状态面只经由这里注入/清理，
# 用例不再直接触碰 layer 内部结构（groups/_online_users 为 InMemory 专属）。


def reset_layer_state(layer):
    """清空 layer 状态：进程级 layer 单例跨用例复用，必须清上一用例遗留。

    InMemory 档重置内部结构；真 Redis 层删除本层 prefix 键空间（group zset 与
    online 反向索引，键形如 asgi{N}:group:* / asgi{N}:online:users），仅触达
    channel 层自身的键空间，与 cache（tN: 前缀）互不影响。
    """
    if _is_memory_layer(layer):
        layer._online_users = {}
        if hasattr(layer, "groups") and hasattr(layer.groups, "clear"):
            layer.groups.clear()
        return

    from asgiref.sync import async_to_sync

    async def _flush():
        for index in range(layer.ring_size):
            connection = layer.connection(index)
            cursor = 0
            while True:
                cursor, keys = await connection.scan(cursor=cursor, match=f"{layer.prefix}:*", count=1000)
                if keys:
                    await connection.delete(*keys)
                if cursor == 0:
                    break

    async_to_sync(_flush)()


def beat_layer(layer, user_pk, channel="chan"):
    """模拟一次前端心跳：update_active_layers 为双栈同名的公开 API。"""
    from asgiref.sync import async_to_sync

    from message.utils import get_user_layer_group_name

    async_to_sync(layer.update_active_layers)(get_user_layer_group_name(user_pk), channel)


def inject_group_channels(layer, group, channels):
    """直接注入组状态（在线明细/降级 SCAN 的读取面），channels 为 channel 名列表。"""
    if _is_memory_layer(layer):
        layer.groups.setdefault(group, {}).update({c: time.time() for c in channels})
        return

    from asgiref.sync import async_to_sync

    async def _inject():
        connection = layer.connection(layer.consistent_hash(group))
        await connection.zadd(layer._group_key(group), {c: time.time() for c in channels})

    async_to_sync(_inject)()


def inject_online_user(layer, user_pk, at=None):
    """注入在线反向索引条目；at 传过去的时间戳可构造「心跳超期」场景。"""
    if _is_memory_layer(layer):
        layer._online_users[user_pk] = at if at is not None else time.time()
        return

    from asgiref.sync import async_to_sync

    async def _inject():
        key = layer.online_users_key
        connection = layer.connection(layer.consistent_hash(key))
        await connection.zadd(key, {str(user_pk): at if at is not None else time.time()})

    async_to_sync(_inject)()
