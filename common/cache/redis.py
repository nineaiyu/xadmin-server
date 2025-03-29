#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : redis
# author : ly_13
# date : 6/2/2023
import json
import time

from django_redis import get_redis_connection

from common.utils import get_logger

logger = get_logger(__name__)


def format_return(data):
    try:
        if isinstance(data, bytes):
            data = data.decode(encoding='utf-8')
        return json.loads(data)
    except:
        return data


def format_input(data):
    try:
        return json.dumps(data)
    except:
        return data


class CacheRedis(object):

    def __init__(self, key):
        self.connect = get_redis_connection("default")
        self.key = key

    def lock(self, *args, **kwargs):
        return self.connect.lock(f"{self.key}_locker", *args, **kwargs)

    def expire(self, timeout=None):
        return self.connect.expire(self.key, timeout)


class CacheList(CacheRedis):

    def __init__(self, key, max_size=1024, timeout=None):
        super().__init__(key)
        self.max_size = max_size
        self.timeout = timeout

    def auto_ltrim(self):
        stop = self.connect.llen(self.key)
        if self.max_size < stop:
            start = stop - self.max_size
            self.connect.ltrim(self.key, start, stop)

    def push(self, json_data, *args):
        self.connect.lpush(self.key, json.dumps(json_data), *[json.dumps(x) for x in args])
        self.auto_ltrim()
        if self.timeout is not None:
            self.connect.expire(self.key, self.timeout)

    def pop(self):
        try:
            b_data = self.connect.rpop(self.key)
            if b_data:
                return json.loads(b_data)
        except Exception as e:
            logger.warning(f"{self.key} pop failed {e}")

    def delete(self):
        self.connect.delete(self.key)

    def len(self):
        return self.connect.llen(self.key)

    def get_all(self):
        return [format_return(k) for k in self.connect.lrange(self.key, 0, -1)]


class CacheSet(CacheRedis):

    def __init__(self, key):
        super().__init__(key)

    def get_all(self):
        return {format_return(k) for k in self.connect.smembers(self.key)}

    def exist(self, val):
        return self.connect.sismember(self.key, val)

    def count(self):
        return format_return(self.connect.scard(self.key))

    def push(self, val, *args):
        return self.connect.sadd(self.key, format_input(val), *[format_input(x) for x in args])

    def pop(self, val):
        try:
            return self.connect.srem(self.key, format_input(val))
        except Exception as e:
            logger.warning(f"{self.key} pop {val} failed {e}")

    def delete(self):
        self.connect.delete(self.key)


class CacheSortedSet(CacheRedis):

    def __init__(self, key):
        super().__init__(key)

    def get_all(self, with_scores=False):
        return self.get_members(0, -1, with_scores)

    def get_members(self, start=0, end=-1, with_scores=False):
        data = self.connect.zrevrange(self.key, start, end, with_scores)
        if with_scores:
            return [{format_return(k[0]): format_return(k[1])} for k in data]
        else:
            return [format_return(k) for k in data]

    def exist(self, val):
        return bool(self.connect.zrank(self.key, val))

    def count(self):
        return format_return(self.connect.zcard(self.key))

    def push(self, val, *args):
        map_data = {}
        if isinstance(val, dict):
            map_data.update(val)
        else:
            map_data[format_input(val)] = format_input(time.time())

        for x in args:
            if isinstance(x, dict):
                map_data.update(x)
            else:
                map_data[format_input(x)] = format_input(time.time())

        return self.connect.zadd(self.key, map_data)

    def pop(self, val):
        try:
            return self.connect.zrem(self.key, format_input(val))
        except Exception as e:
            logger.warning(f"{self.key} pop {val} failed {e}")

    def delete(self):
        self.connect.delete(self.key)


class CacheHash(CacheRedis):

    def __init__(self, key):
        super().__init__(key)

    def get_all(self):
        # return [format_return(v) for v in self.connect.hgetall(self.key).values()]
        data = {}
        for k, v in self.connect.hgetall(self.key).items():
            data[format_return(k)] = format_return(v)
        return data
        # return [{format_return(k): format_return(v)} for k, v in self.connect.hgetall(self.key).items()]

    def get(self, key):
        return format_return(self.connect.hget(self.key, key))

    def count(self):
        return format_return(self.connect.hlen(self.key))

    def push(self, key, val):
        return self.connect.hset(self.key, key, format_input(val))

    def pop(self, val):
        try:
            return self.connect.hdel(self.key, val)
        except Exception as e:
            logger.warning(f"{self.key} pop {val} failed {e}")

    def delete(self):
        self.connect.delete(self.key)


redis_connect = get_redis_connection("default")
