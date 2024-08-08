#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : token
# author : ly_13
# date : 6/2/2023
import logging
import random
import secrets
import string
import time
import uuid

from common.cache.storage import TokenManagerCache, RedisCacheBase

logger = logging.getLogger(__name__)


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


def make_from_user_uuid(uid):
    random_str = uuid.uuid1().__str__().split("-")[0:-1]
    user_ran_str = uuid.uuid5(uuid.NAMESPACE_DNS, str(uid)).__str__().split("-")
    user_ran_str.extend(random_str)
    new_str = "".join(user_ran_str)
    return new_str


def random_replace_char(s, chars, length):
    using_index = set()
    seq = list(s)

    while length > 0:
        index = secrets.randbelow(len(seq) - 1)
        if index in using_index or index == 0:
            continue
        seq[index] = secrets.choice(chars)
        using_index.add(index)
        length -= 1
    return ''.join(seq)


def remove_exclude_char(s, exclude_chars):
    for i in exclude_chars:
        s = s.replace(i, '')
    return s


def random_string(
        length: int, lower=True, upper=True, digit=True,
        special_char=False, exclude_chars='', symbols='!#$%&()*+,-.:;<=>?@[]^_~'
):
    if not any([lower, upper, digit]):
        raise ValueError('At least one of `lower`, `upper`, `digit` must be `True`')
    if length < 4:
        raise ValueError('The length of the string must be greater than 3')

    chars_map = (
        (lower, string.ascii_lowercase),
        (upper, string.ascii_uppercase),
        (digit, string.digits),
    )
    chars = ''.join([i[1] for i in chars_map if i[0]])
    chars = remove_exclude_char(chars, exclude_chars)
    texts = list(secrets.choice(chars) for __ in range(length))
    texts = ''.join(texts)

    # 控制一下特殊字符的数量, 别随机出来太多
    if special_char:
        symbols = remove_exclude_char(symbols, exclude_chars)
        symbol_num = length // 16 + 1
        texts = random_replace_char(texts, symbols, symbol_num)
    return texts
