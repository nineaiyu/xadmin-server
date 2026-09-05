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

    # PERF-08：全局在线用户反向索引（ZSET，member=user_pk, score=最后心跳时间）。
    # 用一条 ZRANGEBYSCORE 替代 SCAN 全库 + 逐 group 往返，在线统计不再与消息吞吐耦合。
    ONLINE_USERS_SUFFIX = "online:users"

    @property
    def online_users_key(self):
        return f"{self.prefix}:{self.ONLINE_USERS_SUFFIX}".encode("utf8")

    def _online_group_prefix(self):
        # 延迟读取配置，保持本模块可被无 settings 的工具导入
        from django.conf import settings

        return f"{settings.CACHE_KEY_TEMPLATE.get('websocket_group_key')}_"

    def user_pk_from_group(self, group):
        """从个人消息推送组名中解析用户 pk；聊天室等非个人组返回 None。"""
        prefix = self._online_group_prefix()
        if group and group.startswith(prefix):
            tail = group[len(prefix):]
            if tail.isdigit():
                return int(tail)
        return None

    @staticmethod
    def _decode(value):
        return value.decode("utf8") if isinstance(value, bytes) else value

    async def group_discard(self, group, channel):
        """
        Removes the channel from the named group if it is in the group;
        does nothing otherwise (does not error)
        """
        assert self.valid_channel_name(channel), "Channel name not valid"
        key = self._group_key(group)
        index = self.consistent_hash(group)
        connection = self.connection(index)
        pipe = connection.pipeline(transaction=True)
        pipe.zrem(key, channel)
        pipe.zcard(key)
        _removed, remaining = await pipe.execute()

        user_pk = self.user_pk_from_group(group)
        if user_pk is not None and remaining == 0:
            # 该用户最后一个连接已断开，移出全局在线索引
            online_connection = self.connection(self.consistent_hash(self.online_users_key))
            await online_connection.zrem(self.online_users_key, str(user_pk))

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
        return [self._decode(x) for x in await connection.zrange(key, 0, -1)]

    async def update_active_layers(self, group, channel):
        """心跳更新（PERF-08/15）：group ZSET 维护与全局在线索引并入一次 pipeline 往返。"""
        key = self._group_key(group)
        index = self.consistent_hash(group)
        connection = self.connection(index)
        now = time.time()
        user_pk = self.user_pk_from_group(group)
        online_key = self.online_users_key
        same_node = index == self.consistent_hash(online_key)

        pipe = connection.pipeline(transaction=True)
        pipe.zremrangebyscore(key, 0, int(now) - self.layer_expire)
        pipe.zadd(key, {channel: now})
        pipe.expire(key, self.group_expiry)
        if user_pk is not None and same_node:
            # 心跳即续期在线索引；TTL 取心跳周期的整数倍，仅用于回收从未再上线的残留项
            pipe.zadd(online_key, {str(user_pk): now})
            pipe.expire(online_key, self.layer_expire * 10)
        await pipe.execute()

        if user_pk is not None and not same_node:
            online_connection = self.connection(self.consistent_hash(online_key))
            await online_connection.zadd(online_key, {str(user_pk): now})
            await online_connection.expire(online_key, self.layer_expire * 10)

    async def get_online_user_pks(self):
        """在线用户 pk 列表：一条 ZRANGEBYSCORE，天然复用 30s 心跳过期语义。"""
        connection = self.connection(self.consistent_hash(self.online_users_key))
        now = time.time()
        rows = await connection.zrangebyscore(
            self.online_users_key, now - self.layer_expire, "+inf"
        )
        result = []
        for row in rows:
            try:
                result.append(int(self._decode(row)))
            except (TypeError, ValueError):
                continue
        return result

    async def get_layers_for_groups(self, groups):
        """批量获取多个 group 的 channel 列表（PERF-08）。

        同一节点的 group 合并进一个 pipeline，单 Redis 部署下整个请求只有一次往返，
        替代旧实现的逐 group 串行 ZREMRANGEBYSCORE + ZRANGE。
        """
        result = {}
        by_index = {}
        for group in groups:
            by_index.setdefault(self.consistent_hash(group), []).append(group)

        for index, group_list in by_index.items():
            connection = self.connection(index)
            expire_score = int(time.time()) - self.layer_expire
            pipe = connection.pipeline(transaction=False)
            for group in group_list:
                key = self._group_key(group)
                pipe.zremrangebyscore(key, 0, expire_score)
                pipe.zrange(key, 0, -1)
            rows = await pipe.execute()
            for pos, group in enumerate(group_list):
                channels = rows[pos * 2 + 1] or []
                result[group] = [self._decode(x) for x in channels]
        return result

    async def get_groups(self):
        """降级路径：SCAN 全库列出全部 group。

        仅在反向索引不可用/为空时调用（例如 Redis 重启后等待首个心跳自愈的窗口）。
        match 收紧到个人组前缀并显式传 COUNT，避免遍历整个 keyspace。
        """
        groups = []
        group = self._group_key(self._online_group_prefix() + "*")
        for index in range(self.ring_size):
            connection = self.connection(index)
            cursor = 0
            while True:
                cursor, keys = await connection.scan(cursor, match=group, count=1000)
                for key in keys:
                    name = self._decode(key).split(":")[-1]
                    if self.user_pk_from_group(name) is not None:
                        groups.append(name)
                if cursor == 0:
                    break
        return groups
