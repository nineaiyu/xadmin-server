#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : storage
# author : ly_13
# date : 6/2/2023

from django.conf import settings
from django.core.cache import cache

from common.utils import get_logger

logger = get_logger(__name__)


class RedisCacheBase(object):
    def __init__(self, cache_key, timeout=600):
        self.cache_key = cache_key
        self._timeout = timeout

    def __getattribute__(self, item):
        if isinstance(item, str) and item != 'cache_key':
            if hasattr(self, "cache_key"):
                logger.debug(f'act:{item} cache_key:{super().__getattribute__("cache_key")}')
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
        if not self.cache_key.endswith('*'):
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
