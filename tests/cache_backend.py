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
import threading

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


# 按锁名隔离的进程内互斥锁表：不同 name 之间互不阻塞，同一 name 具备真实互斥语义，
# 以便 MagicCacheData 的并发场景能够被单测覆盖。
_LOCK_REGISTRY = {}
_LOCK_REGISTRY_GUARD = threading.Lock()


def _get_named_lock(name):
    with _LOCK_REGISTRY_GUARD:
        lock = _LOCK_REGISTRY.get(name)
        if lock is None:
            lock = _LOCK_REGISTRY[name] = threading.Lock()
        return lock


class SimpleLock:
    """进程内互斥锁（按 name 隔离）。

    早前版本 acquire 恒为成功，导致缓存装饰器的并发行为无法被覆盖；
    这里改为真实的 threading.Lock，仅在 timeout/blocking_timeout 上做简化：
    锁不自动过期（与 fakeredis 的行为差异不影响单测断言）。
    """

    def __init__(self, cache, name, timeout=None, blocking_timeout=None):
        self.cache = cache
        self.key = f"__lock__:{name}"
        self.timeout = timeout or 60
        self.blocking_timeout = blocking_timeout
        self._lock = _get_named_lock(self.key)
        self.acquired = False

    def acquire(self, blocking=True, blocking_timeout=None):
        blocking_timeout = blocking_timeout or self.blocking_timeout
        if not blocking:
            self.acquired = self._lock.acquire(blocking=False)
        elif blocking_timeout is not None:
            self.acquired = self._lock.acquire(timeout=blocking_timeout)
        else:
            self._lock.acquire()
            self.acquired = True
        if self.acquired:
            self.cache.set(self.key, "1", self.timeout)
        return self.acquired

    def release(self):
        if self.acquired:
            self.acquired = False
            self.cache.delete(self.key)
            self._lock.release()

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
        return SimpleLock(self, name, timeout or 60, blocking_timeout=kwargs.get("blocking_timeout"))

    def delete_pattern(self, pattern, version=None):
        # self._cache 中的 key 已含 ":1:" 版本前缀，再走 delete_many 会被二次加前缀，
        # 变成"删除空操作但返回 count"的假成功（会让 invalid_cache 类断言失真）。
        # 命中的 key 已是原始条目键，直接用内部 _delete 同时清理 _cache 与 _expire_info
        keys = [k for k in list(self._cache) if fnmatch.fnmatch(k, f"*{pattern}")]
        deleted = 0
        with self._lock:
            for key in keys:
                if self._delete(key):
                    deleted += 1
        return deleted