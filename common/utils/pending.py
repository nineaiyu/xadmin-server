#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : pending
# author : ly_13
# date : 6/2/2023

import time

from django.core.cache import cache

from common.cache.storage import PendingStateCache
from common.utils import get_logger

logger = get_logger(__name__)


def set_pending_cache(unique_key, cache_data, cache_obj, timeout):
    if unique_key in cache_data:
        cache_data.remove(unique_key)
    logger.warning(f'return unique_key:{unique_key}  cache_data: {cache_data}  ')
    cache_obj.set_storage_cache(cache_data, timeout)


def get_pending_result(func, expect_func, loop_count=10, sleep_time=3, unique_key='default_key',
                       run_func_count=2, pop_first=True, *args, **kwargs):
    """
    :param func:            将要运行的函数对象
    :param expect_func:     期待的运行结果函数
    :param loop_count:      执行次数
    :param sleep_time:      执行循环等待时间
    :param unique_key:      11请求唯一标识，用与函数并发请求
    :param run_func_count:  11函数并发请求数，超过该次数，会自动移除多余等待
    :param pop_first:       11超出，是否自动移除最老的请求，True: 移除最老的， False: 移除最新的
    :param args:
    :param kwargs:
    :return:
    """
    locker_key = kwargs.pop('locker_key')
    cache_timeout = loop_count * sleep_time * (run_func_count + 1)
    cache_obj = PendingStateCache(locker_key)
    cache_data = cache_obj.get_storage_cache()
    is_pop = False
    if cache_data and isinstance(cache_data, list):
        if unique_key not in cache_data:
            cache_data.append(unique_key)
            if len(cache_data) > run_func_count and len(cache_data) > 0:
                is_pop = True
                if pop_first:
                    cache_data.pop(0)
                else:
                    cache_data.pop()
    else:
        cache_data = [unique_key]

    cache_obj.set_storage_cache(cache_data, cache_timeout)
    if not pop_first and len(cache_data) == run_func_count and is_pop:
        logger.warning(f'unique_key:{unique_key}  cache_data: {cache_data}  ')
        return True, {'err_msg': '请求重复,请稍后再试'}
    try:
        with cache.lock(f"get_pending_result_{locker_key}", timeout=loop_count * sleep_time):
            count = 1
            while True:
                cache_data = cache_obj.get_storage_cache()
                logger.warning(f'unique_key:{unique_key}  cache_data: {cache_data}  ')
                if cache_data and isinstance(cache_data, list) and unique_key in cache_data:
                    result = func(*args, **kwargs)
                    if expect_func(result, *args, **kwargs):
                        set_pending_cache(unique_key, cache_data, cache_obj, cache_timeout)
                        return True, {'data': result}
                    time.sleep(sleep_time)
                    if loop_count < count:
                        set_pending_cache(unique_key, cache_data, cache_obj, cache_timeout)
                        return False, {'err_msg': '请求超时'}
                    count += 1
                else:
                    set_pending_cache(unique_key, cache_data, cache_obj, cache_timeout)
                    return True, {'err_msg': '请求重复,请稍后再试'}

    except Exception as e:
        logger.warning(f'get pending result exception: {e}')
        set_pending_cache(unique_key, cache_data, cache_obj, cache_timeout)
        return False, {'err_msg': '内部错误'}
