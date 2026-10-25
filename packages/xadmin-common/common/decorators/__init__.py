# -*- coding: utf-8 -*-
"""装饰器按域拆分（cache / debounce / transaction / singleton）。

本包 re-export 全部公开 API，`from common.decorators import xxx` 的历史用法不变；
子模块职责：

- cache：进程内方法级内存缓存（cached_method）+ 分布式缓存装饰器（MagicCacheData /
  MagicCacheResponse，自 base/magic.py 拆入）
- concurrency：分布式锁（run_function_by_locker）/ 失败重试（call_function_try_attempts）/
  窗口限次（magic_call_in_times），自 base/magic.py 拆入
- debounce：延迟执行与合并参数（delay_run / merge_delay_run）及配套事件循环线程/执行池
- transaction：事务提交后执行（on_transaction_commit）
- singleton：类单例（Singleton）

导入本包无副作用：事件循环线程与线程池在首个延迟任务（或显式访问 `executor`）
时才惰性创建。
"""

from typing import Any

from common.decorators.cache import MagicCacheData as MagicCacheData
from common.decorators.cache import MagicCacheResponse as MagicCacheResponse
from common.decorators.cache import cache_response as cache_response
from common.decorators.cache import cached_method
from common.decorators.concurrency import call_function_try_attempts as call_function_try_attempts
from common.decorators.concurrency import magic_call_in_times as magic_call_in_times
from common.decorators.concurrency import magic_wrapper as magic_wrapper
from common.decorators.concurrency import run_function_by_locker as run_function_by_locker
from common.decorators.debounce import (
    Debouncer,
    EventLoopThread,
    cancel_or_remove_debouncer_task,
    default_suffix_key,
    delay_run,
    get_executor,
    get_loop,
    ignore_err_exceptions,
    merge_delay_run,
    run_debouncer_func,
)
from common.decorators.singleton import Singleton
from common.decorators.transaction import on_transaction_commit

__all__ = [
    "Debouncer",
    "EventLoopThread",
    "MagicCacheData",
    "MagicCacheResponse",
    "Singleton",
    "cache_response",
    "call_function_try_attempts",
    "magic_call_in_times",
    "magic_wrapper",
    "run_function_by_locker",
    "cached_method",
    "cancel_or_remove_debouncer_task",
    "default_suffix_key",
    "delay_run",
    "get_executor",
    "get_loop",
    "ignore_err_exceptions",
    "merge_delay_run",
    "on_transaction_commit",
    "run_debouncer_func",
]


def __getattr__(name: str) -> Any:
    """兼容历史模块级 `executor` 引用（原 import 即创建，现首次访问惰性创建）。"""
    if name == "executor":
        return get_executor()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
