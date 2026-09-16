# -*- coding: utf-8 -*-
"""延迟防抖域：延迟执行（delay_run）与合并参数（merge_delay_run）。

配套基础设施「事件循环线程 + 执行池」为**惰性初始化**：一条常驻事件循环线程与
一个 10 线程执行池改为在首个延迟任务（或显式访问 `get_loop()` / `get_executor()`）
时创建——import 本模块不再产生后台线程/线程池副作用。原实现 import 即启动，
所有间接 import 方（含 celery/worker 等）都会常驻这些资源。
"""

import asyncio
import functools
import inspect
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from common.core.db.utils import open_db_connection
from common.utils import get_logger

logger = get_logger(__name__)


class EventLoopThread(threading.Thread):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._loop = asyncio.new_event_loop()

    def run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        except Exception as e:
            logger.error(f"Event loop stopped with err: {e} ")

    def get_loop(self):
        return self._loop


# 惰性状态：None 表示尚未创建（首次调度防抖任务时经双检锁初始化）
_loop_thread = None
_executor = None
_state_lock = threading.Lock()
_loop_debouncer_func_task_cache = {}
_loop_debouncer_func_args_cache = {}
_loop_debouncer_func_task_time_cache = {}


def _get_loop_thread():
    """事件循环线程惰性创建（daemon 线程，不阻塞进程退出）。"""
    global _loop_thread
    if _loop_thread is None:
        with _state_lock:
            if _loop_thread is None:
                thread = EventLoopThread()
                thread.daemon = True
                thread.start()
                _loop_thread = thread
    return _loop_thread


def get_executor():
    """防抖执行池惰性创建（max_workers=10，与原模块级实例同参数）。"""
    global _executor
    if _executor is None:
        with _state_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="debouncer")
    return _executor


def get_loop():
    return _get_loop_thread().get_loop()


def default_suffix_key(*args, **kwargs):
    return "default"


def cancel_or_remove_debouncer_task(cache_key):
    task = _loop_debouncer_func_task_cache.get(cache_key, None)
    if not task:
        return
    if task.done():
        del _loop_debouncer_func_task_cache[cache_key]
    else:
        task.cancel()


def run_debouncer_func(cache_key, ttl, func, *args, **kwargs):
    cancel_or_remove_debouncer_task(cache_key)
    run_func_partial = functools.partial(_run_func, cache_key, func)

    current = time.time()
    first_run_time = _loop_debouncer_func_task_time_cache.get(cache_key, None)
    if first_run_time is None:
        _loop_debouncer_func_task_time_cache[cache_key] = current
        first_run_time = current

    if current - first_run_time > ttl:
        _loop_debouncer_func_args_cache.pop(cache_key, None)
        _loop_debouncer_func_task_time_cache.pop(cache_key, None)
        get_executor().submit(run_func_partial, *args, **kwargs)
        logger.debug(f"pid {os.getpid()} executor submit run {func.__name__}")
        return

    loop = _get_loop_thread().get_loop()
    _debouncer = Debouncer(run_func_partial, lambda: True, ttl, loop=loop, executor=get_executor())
    task = asyncio.run_coroutine_threadsafe(_debouncer(*args, **kwargs), loop=loop)
    _loop_debouncer_func_task_cache[cache_key] = task


class Debouncer:
    def __init__(self, callback, check, delay, loop=None, executor=None):
        self.callback = callback
        self.check = check
        self.delay = delay
        self.loop = loop
        if not loop:
            self.loop = asyncio.get_event_loop()
        self.executor = executor

    async def __call__(self, *args, **kwargs):
        await asyncio.sleep(self.delay)
        ok = await self._run_sync_to_async(self.check)
        if ok:
            callback_func = functools.partial(self.callback, *args, **kwargs)
            return await self._run_sync_to_async(callback_func)

    async def _run_sync_to_async(self, func):
        if asyncio.iscoroutinefunction(func):
            return await func()
        return await self.loop.run_in_executor(self.executor, func)


ignore_err_exceptions = ("(3101, 'Plugin instructed the server to rollback the current transaction.')",)


def _run_func(key, func, *args, **kwargs):
    try:
        with open_db_connection():
            # 保证执行时使用的是新的 connection 数据库连接
            # 避免出现 MySQL server has gone away 的情况
            func(*args, **kwargs)
    except Exception as e:
        msg = str(e)
        log_func = logger.error
        if msg in ignore_err_exceptions:
            log_func = logger.info
        pid = os.getpid()
        thread_name = threading.current_thread()
        log_func(f"pid {pid} thread {thread_name} delay run {func.__name__} error: {msg}")
    _loop_debouncer_func_task_cache.pop(key, None)
    _loop_debouncer_func_args_cache.pop(key, None)
    _loop_debouncer_func_task_time_cache.pop(key, None)


def delay_run(ttl=5, key=None):
    """
    延迟执行函数, 在 ttl 秒内, 只执行最后一次
    :param ttl:
    :param key: 是否合并参数, 一个 callback
    :return:
    """

    def inner(func):
        suffix_key_func = key if key else default_suffix_key
        sigs = inspect.signature(func)
        if len(sigs.parameters) != 0:
            raise ValueError(f"Merge delay run must not arguments: {func.__name__}")

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            func_name = f"{func.__module__}_{func.__name__}"
            key_suffix = suffix_key_func(*args)
            cache_key = f"DELAY_RUN_{func_name}_{key_suffix}"
            run_debouncer_func(cache_key, ttl, func, *args, **kwargs)

        return wrapper

    return inner


def merge_delay_run(ttl=5, key=None):
    """
    延迟执行函数, 在 ttl 秒内, 只执行最后一次, 并且合并参数
    :param ttl:
    :param key: 是否合并参数, 一个 callback
    :return:
    """

    def delay(func, *args, **kwargs):
        # 每次调用 delay 时可以指定本次调用的 ttl
        current_ttl = kwargs.pop("ttl", ttl)
        suffix_key_func = key if key else default_suffix_key
        func_name = f"{func.__module__}_{func.__name__}"
        key_suffix = suffix_key_func(*args, **kwargs)
        cache_key = f"MERGE_DELAY_RUN_{func_name}_{key_suffix}"
        cache_kwargs = _loop_debouncer_func_args_cache.get(cache_key, {})

        for k, v in kwargs.items():
            if not isinstance(v, (tuple, list, set)):
                raise ValueError(f"func kwargs value must be list or tuple: {func.__name__} {v}")
            v = set(v)
            if k not in cache_kwargs:
                cache_kwargs[k] = v
            else:
                cache_kwargs[k] = cache_kwargs[k].union(v)
        _loop_debouncer_func_args_cache[cache_key] = cache_kwargs
        run_debouncer_func(cache_key, current_ttl, func, *args, **cache_kwargs)

    def apply(func, sync=False, *args, **kwargs):
        if sync:
            return func(*args, **kwargs)
        else:
            return delay(func, *args, **kwargs)

    def inner(func):
        sigs = inspect.signature(func)
        if len(sigs.parameters) != 1:
            raise ValueError(f"func must have one arguments: {func.__name__}")
        param = list(sigs.parameters.values())[0]
        if not isinstance(param.default, tuple):
            raise ValueError(f"func default must be tuple: {param.default}")
        func.delay = functools.partial(delay, func)
        func.apply = functools.partial(apply, func)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        return wrapper

    return inner
