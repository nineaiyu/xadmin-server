#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : redis
# author : ly_13
# date : 6/2/2023
import json
import time
from typing import Any

from django_redis import get_redis_connection

from common.utils import get_logger

logger = get_logger(__name__)


def format_return(data: Any) -> Any:
    try:
        if isinstance(data, bytes):
            data = data.decode(encoding="utf-8")
        return json.loads(data)
    except Exception:
        # 非 JSON 缓存值：原样返回（兼容历史/外部写入）
        return data


def format_input(data: Any) -> Any:
    try:
        return json.dumps(data)
    except Exception:
        # 不可 JSON 序列化的值：原样返回，由调用方按需处理
        return data


class CacheRedis:
    def __init__(self, key: str) -> None:
        self.connect = get_redis_connection("default")
        self.key = key

    def lock(self, *args: Any, **kwargs: Any) -> Any:
        return self.connect.lock(f"{self.key}_locker", *args, **kwargs)

    def expire(self, timeout: int | None = None) -> Any:
        return self.connect.expire(self.key, timeout)


class CacheList(CacheRedis):
    def __init__(self, key: str, max_size: int = 1024, timeout: int | None = None) -> None:
        super().__init__(key)
        self.max_size = max_size
        self.timeout = timeout

    def auto_ltrim(self) -> None:
        stop = self.connect.llen(self.key)
        if self.max_size < stop:
            start = stop - self.max_size
            self.connect.ltrim(self.key, start, stop)

    def push(self, json_data: Any, *args: Any) -> None:
        self.connect.lpush(self.key, json.dumps(json_data), *[json.dumps(x) for x in args])
        self.auto_ltrim()
        if self.timeout is not None:
            self.connect.expire(self.key, self.timeout)

    def pop(self) -> Any:
        try:
            b_data = self.connect.rpop(self.key)
            if b_data:
                return json.loads(b_data)
        except Exception as e:
            logger.warning(f"{self.key} pop failed {e}")

    def delete(self) -> None:
        self.connect.delete(self.key)

    def len(self) -> int:
        return int(self.connect.llen(self.key))

    def get_all(self) -> list[Any]:
        return [format_return(k) for k in self.connect.lrange(self.key, 0, -1)]


class CacheSet(CacheRedis):
    def __init__(self, key: str) -> None:
        super().__init__(key)

    def get_all(self) -> set[Any]:
        return {format_return(k) for k in self.connect.smembers(self.key)}

    def exist(self, val: Any) -> bool:
        return bool(self.connect.sismember(self.key, val))

    def count(self) -> Any:
        return format_return(self.connect.scard(self.key))

    def push(self, val: Any, *args: Any) -> Any:
        return self.connect.sadd(self.key, format_input(val), *[format_input(x) for x in args])

    def pop(self, val: Any) -> Any:
        try:
            return self.connect.srem(self.key, format_input(val))
        except Exception as e:
            logger.warning(f"{self.key} pop {val} failed {e}")

    def delete(self) -> None:
        self.connect.delete(self.key)


class CacheSortedSet(CacheRedis):
    def __init__(self, key: str) -> None:
        super().__init__(key)

    def get_all(self, with_scores: bool = False) -> list[Any]:
        return self.get_members(0, -1, with_scores)

    def get_members(self, start: int = 0, end: int = -1, with_scores: bool = False) -> list[Any]:
        data = self.connect.zrevrange(self.key, start, end, with_scores)
        if with_scores:
            return [{format_return(k[0]): format_return(k[1])} for k in data]
        else:
            return [format_return(k) for k in data]

    def exist(self, val: Any) -> bool:
        return bool(self.connect.zrank(self.key, val))

    def count(self) -> Any:
        return format_return(self.connect.zcard(self.key))

    def push(self, val: Any, *args: Any) -> Any:
        map_data: dict[Any, Any] = {}
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

    def pop(self, val: Any) -> Any:
        try:
            return self.connect.zrem(self.key, format_input(val))
        except Exception as e:
            logger.warning(f"{self.key} pop {val} failed {e}")

    def delete(self) -> None:
        self.connect.delete(self.key)


class CacheHash(CacheRedis):
    def __init__(self, key: str) -> None:
        super().__init__(key)

    def get_all(self) -> dict[Any, Any]:
        # return [format_return(v) for v in self.connect.hgetall(self.key).values()]
        data: dict[Any, Any] = {}
        for k, v in self.connect.hgetall(self.key).items():
            data[format_return(k)] = format_return(v)
        return data
        # return [{format_return(k): format_return(v)} for k, v in self.connect.hgetall(self.key).items()]

    def get(self, key: Any) -> Any:
        return format_return(self.connect.hget(self.key, key))

    def count(self) -> Any:
        return format_return(self.connect.hlen(self.key))

    def push(self, key: Any, val: Any) -> Any:
        return self.connect.hset(self.key, key, format_input(val))

    def pop(self, val: Any) -> Any:
        try:
            return self.connect.hdel(self.key, val)
        except Exception as e:
            logger.warning(f"{self.key} pop {val} failed {e}")

    def delete(self) -> None:
        self.connect.delete(self.key)


redis_connect = get_redis_connection("default")
