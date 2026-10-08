#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""通用函数装饰器（自 common/base/magic.py 平移）：连接管理、信号禁停与诊断计时。

对外的既有导入面（common.base.magic）经该模块再导出保持不变。
"""

import time
from functools import wraps

from django.db import close_old_connections, connection

from common.settings_contract import kernel_setting
from common.utils import get_logger

logger = get_logger(__name__)


def handle_db_connections(func):
    @wraps(func)
    def func_wrapper(*args, **kwargs):
        close_old_connections()
        logger.info(f"{func.__name__} run before do close old connection")
        result = func(*args, **kwargs)
        logger.info(f"{func.__name__} run after do close old connection")
        close_old_connections()

        return result

    return func_wrapper


def temporary_disable_signal(signal, receiver, *args, **kwargs):
    """临时禁用信号"""

    def decorator(func):
        @wraps(func)
        def wrapper(*_args, **_kwargs):
            signal.disconnect(*args, receiver=receiver, **kwargs)
            try:
                return func(*_args, **_kwargs)
            finally:
                signal.connect(*args, receiver=receiver, **kwargs)

        return wrapper

    return decorator


def _diagnostics_enabled():
    """诊断装饰器仅在 DEBUG / DEBUG_DEV 下生效。

    ``timeit`` / ``count_sql_queries`` 挂在数据权限过滤这类热路径上，
    生产环境每次都打 INFO 日志、并在每次 SQL 执行上挂钩子，属纯开销。
    """
    return bool(kernel_setting("DEBUG") or kernel_setting("DEBUG_DEV"))


def timeit(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _diagnostics_enabled():
            return func(*args, **kwargs)
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
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _diagnostics_enabled():
            return func(*args, **kwargs)
        sql_counter = SQLCounter()
        with connection.execute_wrapper(sql_counter):
            result = func(*args, **kwargs)
        logger.info(f"{func.__name__} sql queries count: {sql_counter.count}")
        return result

    return wrapper
