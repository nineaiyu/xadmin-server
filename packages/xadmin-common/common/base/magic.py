#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : magic
# author : ly_13
# date : 6/2/2023


import time
from functools import WRAPPER_ASSIGNMENTS, wraps
from importlib import import_module
from typing import Any

from django.core.cache import cache
from django.http.response import HttpResponse
from redis.exceptions import LockError

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


def call_function_try_attempts(try_attempts=3, sleep_time=2, failed_callback=None):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
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
        module_path, class_name = dotted_path.rsplit(".", 1)
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


class MagicCacheData:
    """带占位保护的缓存装饰器。

    相比旧实现的三处关键修正：
    1. ``func`` 抛异常时删除占位并向上传播，**绝不把空结果标记成成功缓存**
       （旧实现会把 ``data=''`` 缓存整个业务 TTL，导致权限缓存为空 24 小时）；
    2. 占位（``status='ready'``）与分布式锁使用**独立于业务 TTL 的短超时**
       ``PLACEHOLDER_TTL``。计算进程崩溃后最多影响 ``PLACEHOLDER_TTL`` 秒，
       而不是像旧实现那样要等完整业务 TTL（``get_user_permission`` 为 24 小时）；
    3. 去掉无上限忙等轮询，等待方在锁上排队；锁超时（<= PLACEHOLDER_TTL）后
       尚未获得锁的调用方抛出锁异常，不会无限期挂起。
    """

    # 占位与锁的超时上限，与业务 TTL 解耦，避免崩溃后长时间不可用
    PLACEHOLDER_TTL = 60

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
                cache_key = f"magic_cache_data_{func.__name__}"
                if key_func:
                    cache_key = f"{cache_key}_{key_func(*args, **kwargs)}"

                cache_time = timeout
                if timeout_func:
                    cache_time = timeout_func(*args, **kwargs)
                valid_time = max(cache_time - invalid_time, 0)
                # 占位/锁的 TTL 取 PLACEHOLDER_TTL 与业务有效期的较小值，至少 1 秒
                placeholder_ttl = max(min(MagicCacheData.PLACEHOLDER_TTL, valid_time), 1)

                def is_valid(res, now):
                    return bool(res) and res.get("status") == "ok" and now - res.get("c_time", 0) < valid_time

                n_time = time.time()
                res = cache.get(cache_key)
                if is_valid(res, n_time):
                    logger.debug(
                        f"exec {func} finished. cache_time:{cache_time} cache_key:{cache_key} cache data exist"
                    )
                    return res["data"]

                with cache.lock(f"locker_{cache_key}", timeout=placeholder_ttl, blocking_timeout=placeholder_ttl + 5):
                    # 双重检查：等待锁期间可能已有其他进程完成计算并写入缓存
                    n_time = time.time()
                    res = cache.get(cache_key)
                    if is_valid(res, n_time):
                        logger.debug(
                            f"exec {func} finished. cache_time:{cache_time} cache_key:{cache_key} cache data exist"
                        )
                        return res["data"]

                    # 占位使用短 TTL：崩溃后最多影响 placeholder_ttl 秒
                    cache.set(cache_key, {"status": "ready", "c_time": n_time}, placeholder_ttl)
                    try:
                        data = func(*args, **kwargs)
                    except Exception as e:
                        # 关键：异常时清除占位，下一次调用重新计算，不缓存空结果
                        cache.delete(cache_key)
                        logger.error(
                            f"exec {func} failed. time:{time.time() - n_time} cache_time:{cache_time} "
                            f"cache_key:{cache_key} Exception:{e}"
                        )
                        raise
                    cache.set(cache_key, {"status": "ok", "c_time": time.time(), "data": data}, cache_time)
                    logger.debug(
                        f"exec {func} finished. time:{time.time() - n_time} cache_time:{cache_time} "
                        f"cache_key:{cache_key}"
                    )
                    return data

            return wrapper

        return decorator

    @staticmethod
    def invalid_cache(key):
        cache_key = f"magic_cache_data_{key}"
        count = cache.delete_pattern(cache_key)
        # 降噪（2029-10 运营基线）：缓存失效按需走写路径高频触发（实测 WARN 级 ~9.6 万行/天），
        # 无运营价值且淹没真实告警——降为 debug，排查缓存行为时按需开启
        logger.debug(f"invalid_cache cache_key:{cache_key} count:{count}")

    @staticmethod
    def invalid_caches(keys):
        delete_keys = [f"magic_cache_data_{key}" for key in keys]
        count = cache.delete_many(delete_keys)
        logger.debug(f"invalid_cache_data cache_key:{delete_keys[0]}... {len(delete_keys)} count. delete count:{count}")


class MagicCacheResponse:
    def __init__(self, timeout=60 * 10, invalid_time=0, key_func=None):
        self.timeout = timeout
        self.key_func = key_func
        self.invalid_time = invalid_time

    @staticmethod
    def invalid_cache(key):
        cache_key = f"magic_cache_response_{key}"
        count = cache.delete_pattern(cache_key)
        # 降噪（2029-10 运营基线）：同 MagicCache，高频 WARN 降为 debug
        logger.debug(f"invalid_response_cache cache_key:{cache_key} count:{count}")

    @staticmethod
    def invalid_caches(keys):
        delete_keys = [f"magic_cache_response_{key}" for key in keys]
        count = cache.delete_many(delete_keys)
        logger.debug(
            f"invalid_response_cache cache_key:{delete_keys[0]}... {len(delete_keys)} count. delete count:{count}"
        )

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

    #: 单飞锁 TTL（秒）：锁只覆盖「一次回源 + 回写」，超时后其它请求自行回源
    LOCK_TTL = 60

    def process_cache_response(self, view_instance, view_method, request, args, kwargs):
        func_key = self.calculate_key(
            view_instance=view_instance, view_method=view_method, request=request, args=args, kwargs=kwargs
        )
        cache_key = "magic_cache_response"
        func_name = f"{view_instance.__class__.__name__}_{view_method.__name__}"
        if func_key:
            cache_key = f"{cache_key}_{func_key}"
        else:
            cache_key = f"{cache_key}_{func_name}"
        timeout = self.calculate_timeout(view_instance=view_instance)
        # no_cache 旁路：代码内标记（export_data）或显式查询参数（监控面板手动刷新）。
        # 仅需登录的只读接口使用，绕过读取并跳过回写，避免刷新拿到窗口内旧数据
        query = getattr(request, "query_params", None) or getattr(request, "GET", {})
        no_cache = bool(getattr(request, "no_cache", False)) or query.get("no_cache") in ("1", "true")

        if no_cache:
            return self._execute_view(
                view_instance, view_method, request, args, kwargs, cache_key, timeout, store=False
            )

        res = self._load_valid(cache_key, timeout)
        if res is not None:
            return self._serve_cached(res, view_instance, func_name, cache_key)

        # 单飞（与 MagicCacheData 同范式）：并发未命中时只让一个请求回源，
        # 其余在锁上排队，拿到锁后二次检查复用首次结果——避免缓存窗口到期瞬间
        # N 个请求同时回源（列表页大查询尤其明显）
        try:
            with cache.lock(f"locker_{cache_key}", timeout=self.LOCK_TTL, blocking_timeout=self.LOCK_TTL + 5):
                res = self._load_valid(cache_key, timeout)
                if res is not None:
                    return self._serve_cached(res, view_instance, func_name, cache_key)
                return self._execute_view(
                    view_instance, view_method, request, args, kwargs, cache_key, timeout, store=True
                )
        except LockError:
            # 等锁超时：读缓存只是优化、不是正确性要求，退化为直接回源（旧行为）
            logger.warning(f"acquire response cache lock timeout, fallback to direct render. key:{cache_key}")
            return self._execute_view(view_instance, view_method, request, args, kwargs, cache_key, timeout, store=True)

    def _load_valid(self, cache_key: str, timeout) -> dict | None:
        """读取未过期缓存载荷（窗口内才命中；no_cache 分支不走这里）。"""
        res = cache.get(cache_key)
        if res and time.time() - res.get("c_time", time.time()) < timeout - self.invalid_time:
            return res
        return None

    def _serve_cached(self, res: dict, view_instance, func_name: str, cache_key: str) -> HttpResponse:
        logger.info(f"exec {func_name} finished. cache_key:{cache_key}  cache data exist")
        content, status, headers = res["data"]
        response = HttpResponse(content=content, status=status)
        response.renderer_context = view_instance.get_renderer_context()
        for k, v in headers.values():
            response[k] = v
        return self._ensure_closable(response)

    def _execute_view(self, view_instance, view_method, request, args, kwargs, cache_key, timeout, store: bool):
        """回源渲染；``store`` 为真且响应非 4xx/5xx 时回写缓存。"""
        n_time = time.time()
        func_name = f"{view_instance.__class__.__name__}_{view_method.__name__}"
        response = view_method(view_instance, request, *args, **kwargs)
        response = view_instance.finalize_response(request, response, *args, **kwargs)
        response.render()

        if store and not response.status_code >= 400:
            data = (response.rendered_content, response.status_code, {k: (k, v) for k, v in response.items()})
            res = {"c_time": n_time, "data": data}
            cache.set(cache_key, res, timeout)
            logger.debug(f"exec {func_name} finished. time:{time.time() - n_time}  cache_key:{cache_key} result:{res}")
        return self._ensure_closable(response)

    @staticmethod
    def _ensure_closable(response: HttpResponse) -> HttpResponse:
        if not hasattr(response, "_closable_objects"):
            response._closable_objects = []
        return response

    def calculate_key(self, view_instance, view_method, request, args, kwargs):
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

# 通用装饰器拆分至 decorators.py（文件行数门禁），此处再导出保持既有导入面
from common.base.decorators import (  # noqa: E402,F401
    SQLCounter,
    count_sql_queries,
    handle_db_connections,
    temporary_disable_signal,
    timeit,
)
