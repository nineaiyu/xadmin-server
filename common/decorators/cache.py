# -*- coding: utf-8 -*-
"""缓存域装饰器：进程内方法级内存缓存。"""

import time
from functools import wraps


def cached_method(ttl=20):
    """
    进程内内存缓存，ttl 为缓存时间，-1 表示永久。

    使用限制（避免误用）：
    - 仅适用于参数可哈希的调用（list/dict/request 等会直接 TypeError）；
    - 缓存只增不主动清理，进程内长期驻留，不要用于大对象或高基数 key；
    - 多进程/多 worker 之间不共享，不保证一致性。
    """
    _cache = {}

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = (func, args, tuple(sorted(kwargs.items())))
            # 检查缓存是否存在且未过期
            if key in _cache and (ttl == -1 or time.time() - _cache[key]["timestamp"] < ttl):
                return _cache[key]["result"]

            # 缓存过期或不存在，执行方法并缓存结果
            result = func(*args, **kwargs)
            _cache[key] = {"result": result, "timestamp": time.time()}
            return result

        return wrapper

    return decorator
