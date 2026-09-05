# -*- coding: utf-8 -*-
"""测试专用 Channels 内存 channel layer。

生产环境使用 common.cache.channel.RedisChannelLayer（带 get_layers /
get_groups / get_online_user_pks / get_layers_for_groups 用于在线状态统计）。
内存版补齐同名方法，以便 message.utils 等模块在测试中可复用，并按
PERF-08 的反向索引语义维护在线用户表。
"""
import time

from channels.layers import InMemoryChannelLayer


class TestInMemoryChannelLayer(InMemoryChannelLayer):
    def __init__(self, **kwargs):
        self.layer_expire = kwargs.pop("layer_expire", 30)
        super().__init__(**kwargs)
        # PERF-08：全局在线用户反向索引（user_pk -> 最后心跳时间戳）
        self._online_users = {}

    async def get_layers(self, group):
        return list(self.groups.get(group, {}).keys())

    async def get_groups(self):
        return list(self.groups.keys())

    async def update_active_layers(self, group, channel):
        # 与 InMemoryChannelLayer 一致：groups 为 {group: {channel: timestamp}}
        self.groups.setdefault(group, {})[channel] = time.time()
        tail = group.rsplit("_", 1)[-1]
        if tail.isdigit():
            self._online_users[int(tail)] = time.time()

    async def group_discard(self, group, channel):
        channels = self.groups.get(group)
        if channels is not None:
            channels.pop(channel, None)
            if not channels:
                self.groups.pop(group, None)
        tail = group.rsplit("_", 1)[-1]
        if tail.isdigit() and not self.groups.get(group):
            self._online_users.pop(int(tail), None)

    async def get_online_user_pks(self):
        now = time.time()
        return [pk for pk, ts in self._online_users.items() if now - ts <= self.layer_expire]

    async def get_layers_for_groups(self, groups):
        return {group: list(self.groups.get(group, {}).keys()) for group in groups}
