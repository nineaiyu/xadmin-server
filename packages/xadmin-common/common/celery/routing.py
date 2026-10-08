# -*- coding: utf-8 -*-
"""Celery 队列路由：框架内置 + 各应用 config.py::TASK_ROUTES 的声明式合并。

`server/settings/libs.py` 将 `CELERY_TASK_ROUTES` 指向本模块的可调用入口——celery
对可调用路由在每次投递时求值（晚于全部任务模块 import），因此二开应用在自身
`config.py` 声明 `TASK_ROUTES = {"app.tasks.xxx": "heavy"}` 即可改变队列归属，
无需修改 settings 工程层文件（与 config.py::URLPATTERNS 的 HTTP 侧约定对称）。
"""

import logging
from functools import lru_cache
from importlib import import_module

from django.apps import apps

logger = logging.getLogger(__name__)

# 框架内置路由：heavy 队列承载导入/导出/批量操作等重任务（background_task_view_set_job），
# 避免慢任务阻塞邮件/短信/站内信等轻量任务；worker 由 start celery_heavy 拉起消费 heavy 队列
BUILTIN_TASK_ROUTES = {
    "common.tasks.background_task_view_set_job": {"queue": "heavy"},
    # Office 转 PDF 预览：CPU 密集型外部进程，禁止占用默认队列
    "system.tasks.convert_office_preview_task": {"queue": "heavy"},
}


@lru_cache(maxsize=1)
def _app_task_routes() -> dict:
    """收集各应用 config.py 的 TASK_ROUTES 声明（应用名 → 任务名 → 队列）。

    仅在首次投递时执行（django.setup 已完成），config.py 的 import 语义与 HTTP 侧
    URL 注入完全一致；结果缓存——改 TASK_ROUTES 需重启进程（与路由表同口径）。
    """
    collected: dict = {}
    for app_config in apps.get_app_configs():
        try:
            module = import_module(f"{app_config.name}.config")
        except ModuleNotFoundError as e:
            # 仅吞掉「无 config.py」；config.py 内部 import 失败必须暴露
            if e.name not in (f"{app_config.name}.config", app_config.name):
                raise
            continue
        declared = getattr(module, "TASK_ROUTES", None)
        if not declared:
            continue
        for task_name, queue in declared.items():
            collected[task_name] = {"queue": queue} if isinstance(queue, str) else dict(queue)
        logger.info(f"auto register {app_config.name} celery task routes: {sorted(declared)}")
    return collected


def celery_task_route(name, *args, task=None, **kwargs):
    """celery task_routes 可调用入口：应用声明优先，框架内置兜底。"""
    return _app_task_routes().get(name) or BUILTIN_TASK_ROUTES.get(name)


def get_all_task_routes() -> dict:
    """静态合并视图（内置 + 应用声明），供需要「枚举全部路由」的消费方使用
    （如定时任务序列化器的队列下拉推导）；单任务解析走 celery_task_route。"""
    return {**BUILTIN_TASK_ROUTES, **_app_task_routes()}
