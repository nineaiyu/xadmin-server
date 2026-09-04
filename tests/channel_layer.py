# -*- coding: utf-8 -*-
"""测试专用 Channels 内存 channel layer。

生产环境使用 common.cache.channel.RedisChannelLayer（带 get_layers /
get_groups / auto_expire_layers 用于在线状态统计）。内存版缺少这些方法，
此处补齐以便 message.utils 等模块在测试中可复用。
"""
from channels.layers import InMemoryChannelLayer


class TestInMemoryChannelLayer(InMemoryChannelLayer):
    async def get_layers(self, group):
        return list(self.groups.get(group, set()))

    async def get_groups(self):
        return list(self.groups.keys())