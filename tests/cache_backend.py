# -*- coding: utf-8 -*-
"""
单元测试专用缓存后端。

MagicCacheData / MagicCacheResponse / CommonResourceIDsCache 依赖 django-redis
特有的 ``cache.lock``、``cache.delete_pattern`` 以及 ``get_redis_connection``，
django 标准的 LocMemCache 不支持这些能力，而 django-redis 6.0 又难以在进程内
接入 fakeredis。这里提供一个进程内等价实现：

- 数据读写复用 LocMemCache（get/set/delete/add/incr 全部一致）
- lock 提供基于 add 的简化互斥（单线程测试环境下与 redis lock 行为一致）
- delete_pattern 以 glob 方式匹配内部键并删除（满足缓存失效逻辑）
- client 属性伪装 django_redis 的 DefaultClient，将 redis 命令转发给
  fakeredis.FakeStrictRedis（common/cache/redis.py 的 CacheList 等使用）
"""
import fnmatch

import fakeredis
from django.core.cache.backends.locmem import LocMemCache


class FakeRedisClientWrapper:
    """伪装 django_redis 的 DefaultClient。"""

    def __init__(self, server, params, backend):
        self._client = fakeredis.FakeStrictRedis()

    def get_client(self, write=True):
        return self._client

    def __getattr__(self, item):
        return getattr(self._client, item)


class SimpleLock:
    """基于 cache.add 的简化锁，测试环境无并发竞争，acquire 总是成功。"""

    def __init__(self, cache, name, timeout):
        self.cache = cache
        self.key = f"__lock__:{name}"
        self.timeout = timeout
        self.acquired = False

    def acquire(self, blocking=True):
        self.cache.set(self.key, "1", self.timeout)
        self.acquired = True
        return True

    def release(self):
        if self.acquired:
            self.cache.delete(self.key)
            self.acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False


class FakeRedisCache(LocMemCache):
    """支持 lock / delete_pattern / client 的进程内缓存后端。"""

    @property
    def client(self):
        if not hasattr(self, "_client_wrapper"):
            self._client_wrapper = FakeRedisClientWrapper(None, {}, self)
        return self._client_wrapper

    def lock(self, name=None, timeout=None, **kwargs):
        return SimpleLock(self, name, timeout or 60)

    def delete_pattern(self, pattern, version=None):
        keys = [k for k in list(self._cache) if fnmatch.fnmatch(k, f"*{pattern}")]
        self.delete_many(keys)
        return len(keys)