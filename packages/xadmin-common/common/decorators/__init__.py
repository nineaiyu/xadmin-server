# -*- coding: utf-8 -*-
"""装饰器按域拆分（cache / debounce / transaction / singleton）。

本包 re-export 全部公开 API，`from common.decorators import xxx` 的历史用法不变；
子模块职责：

- cache：进程内方法级内存缓存（cached_method）
- debounce：延迟执行与合并参数（delay_run / merge_delay_run）及配套事件循环线程/执行池
- transaction：事务提交后执行（on_transaction_commit）
- singleton：类单例（Singleton）

导入本包无副作用：事件循环线程与线程池在首个延迟任务（或显式访问 `executor`）
时才惰性创建。
"""

from common.decorators.cache import cached_method
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
    "Singleton",
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


def __getattr__(name):
    """兼容历史模块级 `executor` 引用（原 import 即创建，现首次访问惰性创建）。"""
    if name == "executor":
        return get_executor()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
