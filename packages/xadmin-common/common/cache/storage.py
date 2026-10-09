#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : storage
# author : ly_13
# date : 6/2/2023

import logging
from collections.abc import Iterable
from typing import Any

from django.core.cache import cache

from common.settings_contract import kernel_required_setting
from common.utils import get_logger

logger = get_logger(__name__)


def _key_template() -> dict[str, Any]:
    """缓存键模板表（内核契约读取的单点包装，避免各处 f-string 内嵌双引号）。"""
    template: dict[str, Any] = kernel_required_setting("CACHE_KEY_TEMPLATE")
    return template


class RedisCacheBase:
    def __init__(self, cache_key: str, timeout: int = 600) -> None:
        self.cache_key = cache_key
        self._timeout = timeout

    def __getattribute__(self, item: str) -> Any:
        # f-string 会先求值再传参，即使日志级别过滤掉输出，字符串拼接开销也逃不掉。
        # 该类被 JWT 黑名单校验等热路径继承，必须用 isEnabledFor 守卫，DEBUG 关闭时零开销。
        if logger.isEnabledFor(logging.DEBUG) and isinstance(item, str) and item != "cache_key":
            if hasattr(self, "cache_key"):
                logger.debug(f"act:{item} cache_key:{super().__getattribute__('cache_key')}")
        return super().__getattribute__(item)

    def get_storage_cache(self, defaults: Any = None) -> Any:
        return cache.get(self.cache_key, defaults)

    def get_storage_key_and_cache(self) -> tuple[str, Any]:
        return self.cache_key, cache.get(self.cache_key)

    def set_storage_cache(self, value: Any, timeout: int = 0) -> Any:
        if isinstance(timeout, int) and timeout == 0:
            timeout = self._timeout
        return cache.set(self.cache_key, value, timeout)

    def append_storage_cache(self, value: Any, timeout: int | None = None) -> Any:
        with cache.lock(f"{self.cache_key}_lock", timeout=60, blocking_timeout=60):
            data = cache.get(self.cache_key, [])
            if not isinstance(data, list):
                # 键上是非列表值（历史数据 / 类型误用）：明确报错，而不是 AttributeError 兜圈子
                raise TypeError(f"append_storage_cache expects a list value, got {type(data).__name__}")
            data.append(value)
            return cache.set(self.cache_key, data, timeout if timeout else self._timeout)

    def del_storage_cache(self) -> Any:
        return cache.delete(self.cache_key)

    def incr(self, amount: int = 1) -> Any:
        return cache.incr(self.cache_key, amount)

    def expire(self, timeout: int) -> Any:
        return cache.expire(self.cache_key, timeout=timeout)

    def iter_keys(self) -> Any:
        # 局部变量拼接通配后缀：不能就地改写 self.cache_key，
        # 否则之后所有 get/set/delete 都会打到带 "*" 的错误键上
        pattern = self.cache_key if self.cache_key.endswith("*") else f"{self.cache_key}*"
        return cache.iter_keys(pattern)

    def get_many(self) -> Any:
        # get_many 入参是键列表：传字符串会被 Django 按字符迭代成一批无关键
        return cache.get_many([self.cache_key])

    def del_many(self) -> int:
        """按通配删除并返回删除数量（旧实现丢弃结果硬编码 True，调用方无法感知失败）。"""
        return int(cache.delete_pattern(self.cache_key) or 0)


class TokenManagerCache(RedisCacheBase):
    def __init__(self, key: str, release_id: Any) -> None:
        self.cache_key = f"{_key_template().get('make_token_key')}_{key.lower()}_{release_id}"
        super().__init__(self.cache_key)


class PendingStateCache(RedisCacheBase):
    def __init__(self, locker_key: str) -> None:
        self.cache_key = f"{_key_template().get('pending_state_key')}_{locker_key}"
        super().__init__(self.cache_key)


class UploadPartInfoCache(RedisCacheBase):
    def __init__(self, locker_key: str) -> None:
        self.cache_key = f"{_key_template().get('upload_part_info_key')}_{locker_key}"
        super().__init__(self.cache_key)


class DownloadUrlCache(RedisCacheBase):
    def __init__(self, drive_id: Any, file_id: Any) -> None:
        self.cache_key = f"{_key_template().get('download_url_key')}_{drive_id}_{file_id}"
        super().__init__(self.cache_key)


class BlackAccessTokenCache(RedisCacheBase):
    def __init__(self, user_id: Any, access_key: str) -> None:
        self.cache_key = f"{_key_template().get('black_access_token_key')}_{user_id}_{access_key}"
        super().__init__(self.cache_key)


class UserTokenRevokedCache(RedisCacheBase):
    """用户级令牌失效时间戳：强制下线时写入，iat 早于该值的 access token 一律拒绝。

    服务端拿不到用户的 access token 清单（黑名单按单 token md5 存），「踢全部会话」
    只能用时间戳比较。TTL 取 access token 寿命 + 缓冲：超过后旧 token 已自然过期，
    无需继续保留该键；refresh 轮换后新签发的 access iat 更新，不受影响。
    """

    def __init__(self, user_id: Any) -> None:
        self.cache_key = f"{_key_template().get('user_token_revoked_key')}_{user_id}"
        lifetime = kernel_required_setting("SIMPLE_JWT").get("ACCESS_TOKEN_LIFETIME")
        timeout = int(lifetime.total_seconds()) + 60 if lifetime else 3660
        super().__init__(self.cache_key, timeout=timeout)

    @classmethod
    def revoke_many(cls, user_ids: Iterable[Any], revoked_at: Any) -> Any:
        """批量写多个用户的失效时间戳：合并为一次 set_many 往返。

        键、值、TTL 均与逐用户 set_storage_cache 一致（批量踢线用），
        仅把 N 次缓存往返合并为一次 pipeline 写。
        """
        caches = [cls(user_id) for user_id in user_ids]
        if not caches:
            return None
        return cache.set_many({c.cache_key: revoked_at for c in caches}, timeout=caches[0]._timeout)


class SessionTokenRevokedCache(RedisCacheBase):
    """会话级令牌失效标记：单会话下线时写入，按 token 自定义 claim sid 精确拒绝。

    与 UserTokenRevokedCache（用户级、按 iat 时间戳）互补：行维度「下线」只踢
    目标会话，不影响该用户其他在用登录。sid 在登录签发时写入 refresh token 的
    自定义 claim（access 派生/refresh 轮换自动继承），因此 refresh 续命也一起失效。
    TTL 与用户级一致（access 寿命 + 缓冲，过后 token 自然过期）。
    """

    def __init__(self, session_pk: Any) -> None:
        self.cache_key = f"{_key_template().get('session_token_revoked_key')}_{session_pk}"
        lifetime = kernel_required_setting("SIMPLE_JWT").get("ACCESS_TOKEN_LIFETIME")
        timeout = int(lifetime.total_seconds()) + 60 if lifetime else 3660
        super().__init__(self.cache_key, timeout=timeout)


class UserSystemConfigCache(RedisCacheBase):
    def __init__(self, prefix_key: str) -> None:
        self.cache_key = f"{_key_template().get('config_key')}_{prefix_key}"
        super().__init__(self.cache_key)


class CommonResourceIDsCache(RedisCacheBase):
    def __init__(self, prefix_key: str) -> None:
        self.cache_key = f"{_key_template().get('common_resource_ids_key')}_{prefix_key}"
        super().__init__(self.cache_key)


class WebSocketMsgResultCache(RedisCacheBase):
    def __init__(self, prefix_key: str) -> None:
        self.cache_key = f"{_key_template().get('websocket_message_result_key')}_{prefix_key}"
        super().__init__(self.cache_key)
