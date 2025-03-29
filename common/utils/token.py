#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : token
# author : ly_13
# date : 6/2/2023
import random
import string
import time
import uuid

from common.cache.storage import TokenManagerCache, RedisCacheBase
from common.utils import get_logger

logger = get_logger(__name__)


def make_token_cache(key, time_limit=60, prefix='', force_new=False, ext_data=None):
    token_cache = TokenManagerCache(prefix, key)
    token_key, token = token_cache.get_storage_key_and_cache()
    if token and not force_new:
        logger.debug(f"make_token cache exists. token:{token} force_new:{force_new} token_key:{token_key}")
        return token
    else:
        random_str = uuid.uuid1().__str__().split("-")[0:-1]
        user_ran_str = uuid.uuid5(uuid.NAMESPACE_DNS, key).__str__().split("-")
        user_ran_str.extend(random_str)
        token = f"tmp_token_{''.join(user_ran_str)}"

        token_cache.set_storage_cache({
            "atime": time.time() + time_limit,
            "data": key
        }, time_limit)
        RedisCacheBase(token).set_storage_cache({
            "atime": time.time() + time_limit,
            "data": key,
            "ext_data": ext_data
        }, time_limit)
        token_cache.set_storage_cache(token, time_limit - 1)
        logger.debug(f"make_token cache not exists. token:{token} force_new:{force_new} token_key:{token_key}")
        return token


def verify_token_cache(token, key, success_once=False):
    try:
        token_cache = RedisCacheBase(token)
        token, values = token_cache.get_storage_key_and_cache()
        if values and key == values.get("data", None):
            logger.debug(f"verify_token token:{token}  key:{key} success")
            if success_once:
                token_cache.del_storage_cache()
            return values
    except Exception as e:
        logger.error(f"verify_token token:{token}  key:{key} failed Exception:{e}")
        return False
    logger.error(f"verify_token token:{token}  key:{key} failed")
    return False


def generate_token_for_medium(medium):
    if medium == 'email':
        return generate_alphanumeric_token_of_length(32)
    elif medium == 'wechat':
        return 'WeChat'
    else:
        return generate_numeric_token_of_length(6)


def generate_numeric_token_of_length(length, random_str=''):
    return "".join([random.choice(string.digits + random_str) for _ in range(length)])


def generate_alphanumeric_token_of_length(length):
    return "".join(
        [random.choice(string.digits + string.ascii_lowercase + string.ascii_uppercase) for _ in range(length)])


def generate_good_token_of_length(length):
    ascii_uppercase = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
    digits = '23456789'
    return "".join([random.choice(digits + ascii_uppercase) for _ in range(length)])
