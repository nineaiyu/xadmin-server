# -*- coding: utf-8 -*-
"""测试专用 Channels 内存 channel layer。

生产环境使用 common.cache.channel.RedisChannelLayer（带 get_layers /
get_groups / get_online_user_pks / get_layers_for_groups 用于在线状态统计）。
内存版补齐同名方法，以便 message.utils 等模块在测试中可复用，并按
反向索引语义维护在线用户表。
"""

import time

from channels.layers import InMemoryChannelLayer


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
