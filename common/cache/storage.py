#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : storage
# author : ly_13
# date : 6/2/2023

import logging

from django.conf import settings
from django.core.cache import cache

from common.utils import get_logger

logger = get_logger(__name__)


class RedisCacheBase(object):
    def __init__(self, cache_key, timeout=600):
        self.cache_key = cache_key
        self._timeout = timeout

    def __getattribute__(self, item):
        # f-string 会先求值再传参，即使日志级别过滤掉输出，字符串拼接开销也逃不掉。
        # 该类被 JWT 黑名单校验等热路径继承，必须用 isEnabledFor 守卫，DEBUG 关闭时零开销。
        if logger.isEnabledFor(logging.DEBUG) and isinstance(item, str) and item != "cache_key":
            if hasattr(self, "cache_key"):
                logger.debug(f"act:{item} cache_key:{super().__getattribute__('cache_key')}")
        return super().__getattribute__(item)

    def get_storage_cache(self, defaults=None):
        return cache.get(self.cache_key, defaults)

    def get_storage_key_and_cache(self):
        return self.cache_key, cache.get(self.cache_key)

    def set_storage_cache(self, value, timeout=0):
        if isinstance(timeout, int) and timeout == 0:
            timeout = self._timeout
        return cache.set(self.cache_key, value, timeout)

    def append_storage_cache(self, value, timeout=None):
        with cache.lock(f"{self.cache_key}_lock", timeout=60, blocking_timeout=60):
            data = cache.get(self.cache_key, [])
            data.append(value)
            return cache.set(self.cache_key, data, timeout if timeout else self._timeout)

    def del_storage_cache(self):
        return cache.delete(self.cache_key)

    def incr(self, amount=1):
        return cache.incr(self.cache_key, amount)

    def expire(self, timeout):
        return cache.expire(self.cache_key, timeout=timeout)

    def iter_keys(self):
        if not self.cache_key.endswith("*"):
            self.cache_key = f"{self.cache_key}*"
        return cache.iter_keys(self.cache_key)

    def get_many(self):
        return cache.get_many(self.cache_key)

    def del_many(self):
        cache.delete_pattern(self.cache_key)
        return True


class TokenManagerCache(RedisCacheBase):
    def __init__(self, key, release_id):
        self.cache_key = f"{settings.CACHE_KEY_TEMPLATE.get('make_token_key')}_{key.lower()}_{release_id}"
        super().__init__(self.cache_key)


class PendingStateCache(RedisCacheBase):
    def __init__(self, locker_key):
        self.cache_key = f"{settings.CACHE_KEY_TEMPLATE.get('pending_state_key')}_{locker_key}"
        super().__init__(self.cache_key)


class UploadPartInfoCache(RedisCacheBase):
    def __init__(self, locker_key):
        self.cache_key = f"{settings.CACHE_KEY_TEMPLATE.get('upload_part_info_key')}_{locker_key}"
        super().__init__(self.cache_key)


class DownloadUrlCache(RedisCacheBase):
    def __init__(self, drive_id, file_id):
        self.cache_key = f"{settings.CACHE_KEY_TEMPLATE.get('download_url_key')}_{drive_id}_{file_id}"
        super().__init__(self.cache_key)


class BlackAccessTokenCache(RedisCacheBase):
    def __init__(self, user_id, access_key):
        self.cache_key = f"{settings.CACHE_KEY_TEMPLATE.get('black_access_token_key')}_{user_id}_{access_key}"
        super().__init__(self.cache_key)


class UserTokenRevokedCache(RedisCacheBase):
    """用户级令牌失效时间戳：强制下线时写入，iat 早于该值的 access token 一律拒绝。

    服务端拿不到用户的 access token 清单（黑名单按单 token md5 存），「踢全部会话」
    只能用时间戳比较。TTL 取 access token 寿命 + 缓冲：超过后旧 token 已自然过期，
    无需继续保留该键；refresh 轮换后新签发的 access iat 更新，不受影响。
    """

    def __init__(self, user_id):
        self.cache_key = f"{settings.CACHE_KEY_TEMPLATE.get('user_token_revoked_key')}_{user_id}"
        lifetime = settings.SIMPLE_JWT.get("ACCESS_TOKEN_LIFETIME")
        timeout = int(lifetime.total_seconds()) + 60 if lifetime else 3660
        super().__init__(self.cache_key, timeout=timeout)


class SessionTokenRevokedCache(RedisCacheBase):
    """会话级令牌失效标记：单会话下线时写入，按 token 自定义 claim sid 精确拒绝。

    与 UserTokenRevokedCache（用户级、按 iat 时间戳）互补：行维度「下线」只踢
    目标会话，不影响该用户其他在用登录。sid 在登录签发时写入 refresh token 的
    自定义 claim（access 派生/refresh 轮换自动继承），因此 refresh 续命也一起失效。
    TTL 与用户级一致（access 寿命 + 缓冲，过后 token 自然过期）。
    """

    def __init__(self, session_pk):
        self.cache_key = f"{settings.CACHE_KEY_TEMPLATE.get('session_token_revoked_key')}_{session_pk}"
        lifetime = settings.SIMPLE_JWT.get("ACCESS_TOKEN_LIFETIME")
        timeout = int(lifetime.total_seconds()) + 60 if lifetime else 3660
        super().__init__(self.cache_key, timeout=timeout)


class UserSystemConfigCache(RedisCacheBase):
    def __init__(self, prefix_key):
        self.cache_key = f"{settings.CACHE_KEY_TEMPLATE.get('config_key')}_{prefix_key}"
        super().__init__(self.cache_key)


class CommonResourceIDsCache(RedisCacheBase):
    def __init__(self, prefix_key):
        self.cache_key = f"{settings.CACHE_KEY_TEMPLATE.get('common_resource_ids_key')}_{prefix_key}"
        super().__init__(self.cache_key)


class WebSocketMsgResultCache(RedisCacheBase):
    def __init__(self, prefix_key):
        self.cache_key = f"{settings.CACHE_KEY_TEMPLATE.get('websocket_message_result_key')}_{prefix_key}"
        super().__init__(self.cache_key)
