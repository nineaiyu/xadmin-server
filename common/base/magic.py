#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : magic
# author : ly_13
# date : 6/2/2023


import time
from functools import wraps, WRAPPER_ASSIGNMENTS
from importlib import import_module

from django.core.cache import cache
from django.db import close_old_connections, connection
from django.http.response import HttpResponse

from common.utils import get_logger

logger = get_logger(__name__)


def run_function_by_locker(timeout=60 * 5, lock_func=None):
    """
    :param timeout:
    :param lock_func:  func -> {'locker_key':''}
    :return:
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            if lock_func:
                locker = lock_func(*args, **kwargs)
            else:
                locker = kwargs.get('locker', {})
                if locker:
                    kwargs.pop('locker')
            t_locker = {'timeout': timeout, 'locker_key': func.__name__}
            t_locker.update(locker)
            new_locker_key = t_locker.pop('locker_key')
            new_timeout = t_locker.pop('timeout')
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


def call_function_try_attempts(try_attempts=3, sleep_time=2, failed_callback=None):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            res = False, {}
            start_time = time.time()
            for i in range(try_attempts):
                res = func(*args, **kwargs)
                status, result = res
                if status:
                    return res
                else:
                    logger.warning(f'exec {func} failed. {try_attempts} times in total. now {sleep_time} later try '
                                   f'again...{i}')
                time.sleep(sleep_time)
            if not res[0]:
                logger.error(f'exec {func} failed after the maximum number of attempts. Failed:{res[1]}')
                if failed_callback:
                    logger.error(f'exec {func} failed and exec failed callback {failed_callback.__name__}')
                    failed_callback(*args, **kwargs, result=res)
            logger.debug(f"exec {func} finished. time:{time.time() - start_time} result:{res}")
            return res

        return wrapper

    return decorator


def magic_wrapper(func, *args, **kwargs):
    @wraps(func)
    def wrapper():
        return func(*args, **kwargs)

    return wrapper


def import_from_string(dotted_path):
    """
    Import a dotted module path and return the attribute/class designated by the
    last name in the path. Raise ImportError if the import failed.
    """
    try:
        module_path, class_name = dotted_path.rsplit('.', 1)
    except ValueError as err:
        raise ImportError(f"{dotted_path} doesn't look like a module path") from err

    module = import_module(module_path)

    try:
        return getattr(module, class_name)
    except AttributeError as err:
        raise ImportError(f'Module "{module_path}" does not define a "{class_name}" attribute/class') from err


def magic_call_in_times(call_time=24 * 3600, call_limit=6, key=None):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = f'magic_call_in_times_{func.__name__}'
            if key:
                cache_key = f'{cache_key}_{key(*args, **kwargs)}'
            cache_data = cache.get(cache_key)
            if cache_data:
                if cache_data > call_limit:
                    err_msg = f'{func} not yet started. cache_key:{cache_key} call over limit {call_limit} in {call_time}'
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
                    f"exec {func} finished. time:{time.time() - start_time}  cache_key:{cache_key} result:{res}")
                status = True
            except Exception as e:
                res = str(e)
                logger.info(f"exec {func} failed. time:{time.time() - start_time}  cache_key:{cache_key} Exception:{e}")
                status = False

            return status, res

        return wrapper

    return decorator


class MagicCacheData(object):
    @staticmethod
    def make_cache(timeout=60 * 10, invalid_time=0, key_func=None, timeout_func=None):
        """
        :param timeout_func:
        :param timeout:  数据缓存的时候，单位秒
        :param invalid_time: 数据缓存提前失效时间，单位秒。该cache有效时间为 cache_time-invalid_time
        :param key_func: cache唯一标识，默认为所装饰函数名称
        :return:
        """

        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                cache_key = f'magic_cache_data_{func.__name__}'
                if key_func:
                    cache_key = f'{cache_key}_{key_func(*args, **kwargs)}'

                cache_time = timeout
                if timeout_func:
                    cache_time = timeout_func(*args, **kwargs)
                n_time = time.time()
                res = cache.get(cache_key)
                if res:
                    while not res or res.get('status') != 'ok':
                        time.sleep(0.5)
                        logger.warning(
                            f'exec {func} wait. data status is not ok. cache_time:{cache_time} cache_key:{cache_key}  cache data exist result:{res}')
                        res = cache.get(cache_key)
                with cache.lock(f"locker_{cache_key}", timeout=cache_time - invalid_time):
                    if res and n_time - res.get('c_time', n_time) < cache_time - invalid_time:
                        logger.debug(
                            f"exec {func} finished. cache_time:{cache_time} cache_key:{cache_key} cache data exist result:{res}")
                        return res['data']
                    else:
                        res = {'c_time': n_time, 'data': '', 'status': 'ready'}
                        cache.set(cache_key, res, cache_time)
                        try:
                            res['data'] = func(*args, **kwargs)
                            logger.debug(
                                f"exec {func} finished. time:{time.time() - n_time} cache_time:{cache_time} cache_key:{cache_key} result:{res}")
                        except Exception as e:
                            logger.error(
                                f"exec {func} failed. time:{time.time() - n_time}  cache_time:{cache_time} cache_key:{cache_key} Exception:{e}")

                        res['status'] = 'ok'
                        cache.set(cache_key, res, cache_time)

                        return res['data']

            return wrapper

        return decorator

    @staticmethod
    def invalid_cache(key):
        cache_key = f'magic_cache_data_{key}'
        count = cache.delete_pattern(cache_key)
        logger.warning(f"invalid_cache cache_key:{cache_key} count:{count}")

    @staticmethod
    def invalid_caches(keys):
        delete_keys = [f'magic_cache_data_{key}' for key in keys]
        count = cache.delete_many(delete_keys)
        logger.warning(
            f"invalid_cache_data cache_key:{delete_keys[0]}... {len(delete_keys)} count. delete count:{count}")


class MagicCacheResponse(object):
    def __init__(self, timeout=60 * 10, invalid_time=0, key_func=None):
        self.timeout = timeout
        self.key_func = key_func
        self.invalid_time = invalid_time

    @staticmethod
    def invalid_cache(key):
        cache_key = f'magic_cache_response_{key}'
        count = cache.delete_pattern(cache_key)
        logger.warning(f"invalid_response_cache cache_key:{cache_key} count:{count}")

    @staticmethod
    def invalid_caches(keys):
        delete_keys = [f'magic_cache_response_{key}' for key in keys]
        count = cache.delete_many(delete_keys)
        logger.warning(
            f"invalid_response_cache cache_key:{delete_keys[0]}... {len(delete_keys)} count. delete count:{count}")

    def __call__(self, func):
        this = self

        @wraps(func, assigned=WRAPPER_ASSIGNMENTS)
        def inner(self, request, *args, **kwargs):
            return this.process_cache_response(
                view_instance=self,
                view_method=func,
                request=request,
                args=args,
                kwargs=kwargs,
            )

        return inner

    def process_cache_response(self,
                               view_instance,
                               view_method,
                               request,
                               args,
                               kwargs):
        func_key = self.calculate_key(
            view_instance=view_instance,
            view_method=view_method,
            request=request,
            args=args,
            kwargs=kwargs
        )
        cache_key = 'magic_cache_response'
        func_name = f'{view_instance.__class__.__name__}_{view_method.__name__}'
        if func_key:
            cache_key = f'{cache_key}_{func_key}'
        else:
            cache_key = f'{cache_key}_{func_name}'
        timeout = self.calculate_timeout(view_instance=view_instance)
        n_time = time.time()
        if getattr(request, 'no_cache', False):
            res = None
        else:
            res = cache.get(cache_key)
        if res and n_time - res.get('c_time', n_time) < timeout - self.invalid_time:
            logger.info(f"exec {func_name} finished. cache_key:{cache_key}  cache data exist")
            content, status, headers = res['data']
            response = HttpResponse(content=content, status=status)
            response.renderer_context = view_instance.get_renderer_context()
            for k, v in headers.values():
                response[k] = v
        else:
            response = view_method(view_instance, request, *args, **kwargs)
            response = view_instance.finalize_response(request, response, *args, **kwargs)
            response.render()

            if not response.status_code >= 400 and not getattr(request, 'no_cache', False):
                data = (
                    response.rendered_content,
                    response.status_code,
                    {k: (k, v) for k, v in response.items()}
                )
                res = {'c_time': n_time, 'data': data}
                cache.set(cache_key, res, timeout)
                logger.debug(
                    f"exec {func_name} finished. time:{time.time() - n_time}  cache_key:{cache_key} result:{res}")

        if not hasattr(response, '_closable_objects'):
            response._closable_objects = []

        return response

    def calculate_key(self,
                      view_instance,
                      view_method,
                      request,
                      args,
                      kwargs):
        if isinstance(self.key_func, str):
            key_func = getattr(view_instance, self.key_func)
        else:
            key_func = self.key_func
        if key_func:
            return key_func(
                view_instance=view_instance,
                view_method=view_method,
                request=request,
                args=args,
                kwargs=kwargs,
            )

    def calculate_timeout(self, view_instance, **_):
        if isinstance(self.timeout, str):
            self.timeout = getattr(view_instance, self.timeout)
        return self.timeout


cache_response = MagicCacheResponse


def handle_db_connections(func):
    @wraps(func)
    def func_wrapper(*args, **kwargs):
        close_old_connections()
        logger.info(f'{func.__name__} run before do close old connection')
        result = func(*args, **kwargs)
        logger.info(f'{func.__name__} run after do close old connection')
        close_old_connections()

        return result

    return func_wrapper


def temporary_disable_signal(signal, receiver, *args, **kwargs):
    """临时禁用信号"""

    def decorator(func):
        @wraps(func)
        def wrapper(*_args, **_kwargs):
            signal.disconnect(receiver=receiver, *args, **kwargs)
            try:
                return func(*_args, **_kwargs)
            finally:
                signal.connect(receiver=receiver, *args, **kwargs)

        return wrapper

    return decorator


import functools


def timeit(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        logger.info(f"{func.__name__} run time:{end_time - start_time}")
        return result

    return wrapper


class SQLCounter:
    def __init__(self):
        self.count = 0

    def __call__(self, execute, sql, params, many, context):
        self.count += 1
        return execute(sql, params, many, context)


def count_sql_queries(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        sql_counter = SQLCounter()
        with connection.execute_wrapper(sql_counter):
            result = func(*args, **kwargs)
        logger.info(f"{func.__name__} sql queries count: {sql_counter.count}")
        return result

    return wrapper
