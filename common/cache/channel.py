#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : channel
# author : ly_13
# date : 3/29/2025
import time

from channels_redis.core import RedisChannelLayer as _RedisChannelLayer


class RedisChannelLayer(_RedisChannelLayer):
    layer_expire = 30  # 需要心跳方式发送在线状态，否则将channel移除

    async def group_discard(self, group, channel):
        """
        Removes the channel from the named group if it is in the group;
        does nothing otherwise (does not error)
        """
        assert self.valid_channel_name(channel), "Channel name not valid"
        connection, key = await self.auto_expire_layers(group)
        await connection.zrem(key, channel)

    async def auto_expire_layers(self, group):
        assert self.valid_group_name(group), "Group name not valid"
        key = self._group_key(group)
        connection = self.connection(self.consistent_hash(group))

        # Discard old channels based on group_expiry
        await connection.zremrangebyscore(
            key, min=0, max=int(time.time()) - self.layer_expire
        )

        return connection, key

    async def get_layers(self, group):
        connection, key = await self.auto_expire_layers(group)
        return [x.decode("utf8") for x in await connection.zrange(key, 0, -1)]

    async def update_active_layers(self, group, channel):
        connection, key = await self.auto_expire_layers(group)
        await connection.zadd(key, {channel: time.time()})
        await connection.expire(key, self.group_expiry)

    async def get_groups(self):
        groups = []
        group = self._group_key("*")
        for index in range(self.ring_size):
            connection = self.connection(index)
            cursor = 0
            while True:
                cursor, keys = await connection.scan(cursor, match=group)
                for key in keys:
                    groups.append(key.decode("utf8").split(":")[-1])
                if cursor == 0:
                    break
        return groups
