#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""执行控制装饰器：分布式锁 / 失败重试 / 窗口限次（自 base/magic.py 拆出）。"""

import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.core.cache import cache

from common.utils import get_logger

logger = get_logger(__name__)


def run_function_by_locker(
    timeout: int = 60 * 5, lock_func: Callable[..., Any] | None = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    :param timeout:
    :param lock_func:  func -> {'locker_key':''}
    :return:
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.time()
            if lock_func:
                locker = lock_func(*args, **kwargs)
            else:
                locker = kwargs.get("locker", {})
                if locker:
                    kwargs.pop("locker")
            t_locker = {"timeout": timeout, "locker_key": func.__name__}
            t_locker.update(locker)
            new_locker_key = t_locker.pop("locker_key")
            new_timeout = t_locker.pop("timeout")
            if locker and new_timeout and new_locker_key:
                with cache.lock(new_locker_key, timeout=new_timeout, **t_locker):
                    logger.info(f"{new_locker_key} exec {func} start. now time:{time.time()}")
                    res = func(*args, **kwargs)
            else:
                res = func(*args, **kwargs)
            logger.debug(f"{new_locker_key} exec {func} finished. used time:{time.time() - start_time} result:{res}")
            return res

        return wrapper

    return decorator


def call_function_try_attempts(
    try_attempts: int = 3, sleep_time: int = 2, failed_callback: Callable[..., Any] | None = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            res: tuple[bool, Any] = (False, {})
            start_time = time.time()
            for i in range(try_attempts):
                res = func(*args, **kwargs)
                status, result = res
                if status:
                    return res
                else:
                    logger.warning(
                        f"exec {func} failed. {try_attempts} times in total. now {sleep_time} later try again...{i}"
                    )
                time.sleep(sleep_time)
            if not res[0]:
                logger.error(f"exec {func} failed after the maximum number of attempts. Failed:{res[1]}")
                if failed_callback:
                    logger.error(f"exec {func} failed and exec failed callback {failed_callback.__name__}")
                    failed_callback(*args, **kwargs, result=res)
            logger.debug(f"exec {func} finished. time:{time.time() - start_time} result:{res}")
            return res

        return wrapper

    return decorator


def magic_wrapper(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Callable[[], Any]:
    @wraps(func)
    def wrapper() -> Any:
        return func(*args, **kwargs)

    return wrapper


def magic_call_in_times(
    call_time: int = 24 * 3600, call_limit: int = 6, key: Callable[..., Any] | None = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            cache_key = f"magic_call_in_times_{func.__name__}"
            if key:
                cache_key = f"{cache_key}_{key(*args, **kwargs)}"
            cache_data = cache.get(cache_key)
            if cache_data:
                if cache_data > call_limit:
                    err_msg = (
                        f"{func} not yet started. cache_key:{cache_key} call over limit {call_limit} in {call_time}"
                    )
                    logger.warning(err_msg)
                    return False, err_msg
                else:
                    cache.incr(cache_key, 1)
            else:
                cache.set(cache_key, 1, call_time)
            start_time = time.time()
            try:
                res = func(*args, **kwargs)
                logger.debug(
                    f"exec {func} finished. time:{time.time() - start_time}  cache_key:{cache_key} result:{res}"
                )
                status = True
            except Exception as e:
                res = str(e)
                logger.info(f"exec {func} failed. time:{time.time() - start_time}  cache_key:{cache_key} Exception:{e}")
                status = False

            return status, res

        return wrapper

    return decorator
