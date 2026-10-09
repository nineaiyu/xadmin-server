#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : token
# author : ly_13
# date : 6/2/2023
import hashlib
import secrets
import string
import time
import uuid
from typing import Any

from common.cache.storage import RedisCacheBase, TokenManagerCache
from common.utils import get_logger

logger = get_logger(__name__)

#: 临时令牌缓存键前缀（键体为令牌摘要；令牌原文是凭据，不落缓存键与日志）
TEMP_TOKEN_CACHE_PREFIX = "auth_temp_token"


def temp_token_cache_key(token: Any) -> str:
    """令牌 → 缓存键：``auth_temp_token_<sha256 前 32 位>``。

    缓存键会出现在 Redis 键空间、慢查询日志与监控面板里；用摘要替代原文可避免
    凭据外泄，键长度也可控。读取侧对升级前以原文为键的存量令牌做一次迁移兼容。
    """
    digest = hashlib.sha256(str(token).encode("utf-8")).hexdigest()[:32]
    return f"{TEMP_TOKEN_CACHE_PREFIX}_{digest}"


def make_token_cache(
    key: str, time_limit: int = 60, prefix: str = "", force_new: bool = False, ext_data: Any = None
) -> Any:
    token_cache = TokenManagerCache(prefix, key)
    token_key, token = token_cache.get_storage_key_and_cache()
    if token and not force_new:
        logger.debug(f"make_token cache exists. token:{token} force_new:{force_new} token_key:{token_key}")
        return token
    else:
        # 随机段用 secrets 生成（旧实现 uuid1 含网卡 MAC 与时间戳，属信息泄露面，S8）
        random_str = secrets.token_urlsafe(16)
        user_ran_str = uuid.uuid5(uuid.NAMESPACE_DNS, key).__str__().split("-")
        token = f"tmp_token_{''.join(user_ran_str)}{random_str}"

        # 此处不再先写一次 {atime, data} 再被下方 token 覆盖（历史死写，白付一次 Redis 往返）
        cache_key = temp_token_cache_key(token)
        RedisCacheBase(cache_key).set_storage_cache(
            {"atime": time.time() + time_limit, "data": key, "ext_data": ext_data}, time_limit
        )
        token_cache.set_storage_cache(token, time_limit - 1)
        logger.debug(f"make_token cache not exists. cache_key:{cache_key} force_new:{force_new} token_key:{token_key}")
        return token


def verify_token_cache(token: Any, key: str, success_once: bool = False) -> Any:
    cache_key = temp_token_cache_key(token)
    try:
        token_cache = RedisCacheBase(cache_key)
        _, values = token_cache.get_storage_key_and_cache()
        if values is None:
            # 兼容窗口：升级前签发的令牌以原文为键，命中后迁移到摘要键
            # （旧键随自身 TTL 自然消失；迁移幂等，仅存量令牌各发生一次）
            legacy = RedisCacheBase(token)
            _, values = legacy.get_storage_key_and_cache()
            if values is not None:
                remaining = int(float(values.get("atime") or 0) - time.time())
                if remaining > 0:
                    token_cache.set_storage_cache(values, remaining)
                legacy.del_storage_cache()
        if values and key == values.get("data", None):
            logger.debug(f"verify_token cache_key:{cache_key} success")
            if success_once:
                token_cache.del_storage_cache()
            return values
    except Exception as e:
        logger.error(f"verify_token cache_key:{cache_key} failed Exception:{e}")
        return False
    logger.error(f"verify_token cache_key:{cache_key} failed")
    return False


def generate_token_for_medium(medium: Any) -> Any:
    """按通道生成临时令牌。

    ``wechat`` 无独立生成器，显式拒绝——历史实现返回常量 ``"WeChat"``，
    若被当作令牌使用等于零强度凭证。
    """
    if medium == "email":
        return generate_alphanumeric_token_of_length(32)
    elif medium == "wechat":
        raise ValueError("wechat medium has no token generator")
    else:
        return generate_numeric_token_of_length(6)


def generate_numeric_token_of_length(length: Any, random_str: str = "") -> Any:
    # 验证码 / 临时令牌必须用密码学随机源（random.choice 是可预测的 Mersenne Twister）
    return "".join([secrets.choice(string.digits + random_str) for _ in range(length)])


def generate_alphanumeric_token_of_length(length: Any) -> Any:
    return "".join(
        [secrets.choice(string.digits + string.ascii_lowercase + string.ascii_uppercase) for _ in range(length)]
    )


def generate_good_token_of_length(length: Any) -> Any:
    ascii_uppercase = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    digits = "23456789"
    return "".join([secrets.choice(digits + ascii_uppercase) for _ in range(length)])
