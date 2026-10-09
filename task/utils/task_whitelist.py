#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""可手动执行任务白名单：周期任务创建与「立即执行」的安全阀。

celery 注册表里的任意任务（含删数据、改密、发信等系统任务）原先都可经任务
管理页配成周期任务或「立即运行」，等于把全部 ``@shared_task`` 暴露给管理面
误触 / 越权执行。白名单机制：

- 清单入库为可配置项（SysConfig ``MANUAL_RUNNABLE_TASKS``，JSON 字符串数组），
  未登记前默认仅放行演示任务（业务方确认范围后在系统配置页扩容）；
- 支持精确任务名与 fnmatch 通配符（如 ``system.tasks.cleanup_*``）；
- 校验点：PeriodicTask 写入侧（serializers.task）与执行侧（task_periodic 的
  run / batch-run）双重拦截——只挡写入不挡执行会让存量任务绕过白名单。
"""

import fnmatch
from typing import Any


def manual_runnable_tasks() -> tuple[str, ...]:
    """白名单清单（去重保序）：SysConfig 优先，未配置回退 settings 默认值。"""
    from common.core.config import SysConfig

    value = SysConfig.MANUAL_RUNNABLE_TASKS
    if isinstance(value, str):
        items = value.replace("\n", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = []
    cleaned = [str(item).strip() for item in items]
    return tuple(dict.fromkeys(item for item in cleaned if item))


def is_task_runnable(name: Any) -> bool:
    """任务名是否命中白名单（精确或通配符；空清单 = 默认拒绝全部）。"""
    name = str(name or "")
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in manual_runnable_tasks())
