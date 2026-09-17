#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : decorator
# author : ly_13
# date : 9/14/2024

from functools import wraps

_need_registered_period_tasks = []
_after_app_ready_start_tasks = []
_after_app_shutdown_clean_periodic_tasks = []


def add_register_period_task(task):
    _need_registered_period_tasks.append(task)


def get_register_period_tasks():
    return _need_registered_period_tasks


def add_after_app_shutdown_clean_task(name):
    _after_app_shutdown_clean_periodic_tasks.append(name)


def get_after_app_shutdown_clean_tasks():
    return _after_app_shutdown_clean_periodic_tasks


def add_after_app_ready_task(name):
    _after_app_ready_start_tasks.append(name)


def get_after_app_ready_tasks():
    return _after_app_ready_start_tasks


def register_as_period_task(crontab=None, interval=None, name=None, args=(), kwargs=None, description="", module=None):
    """
    Warning: Task must have not any args and kwargs
    :param crontab:  "* * * * *"
    :param interval:  60*60*60
    :param args: ()
    :param kwargs: {}
    :param description: "
    :param name: ""
    :param module: 归属功能模块 id（见 common/core/modules.py）：模块被停用时该任务
        不注册（历史注册条目在启动时清理），重新启用自动恢复；None = 内核任务
    :return:
    """
    if crontab is None and interval is None:
        raise SyntaxError("Must set crontab or interval one")

    def decorate(func):
        if crontab is None and interval is None:
            raise SyntaxError("Interval and crontab must set one")

        # Because when this decorator run, the task was not created,
        # So we can't use func.name
        task = f"{func.__module__}.{func.__name__}"
        _name = name if name else task
        add_register_period_task(
            {
                _name: {
                    "task": task,
                    "interval": interval,
                    "crontab": crontab,
                    "args": args,
                    "kwargs": kwargs if kwargs else {},
                    "description": description,
                    "module": module,
                }
            }
        )

        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        return wrapper

    return decorate


def after_app_ready_start(func):
    # Because when this decorator run, the task was not created,
    # So we can't use func.name
    name = f"{func.__module__}.{func.__name__}"
    if name not in _after_app_ready_start_tasks:
        add_after_app_ready_task(name)

    @wraps(func)
    def decorate(*args, **kwargs):
        return func(*args, **kwargs)

    return decorate


def after_app_shutdown_clean_periodic(func):
    # Because when this decorator run, the task was not created,
    # So we can't use func.name
    name = f"{func.__module__}.{func.__name__}"
    if name not in _after_app_shutdown_clean_periodic_tasks:
        add_after_app_shutdown_clean_task(name)

    @wraps(func)
    def decorate(*args, **kwargs):
        return func(*args, **kwargs)

    return decorate
