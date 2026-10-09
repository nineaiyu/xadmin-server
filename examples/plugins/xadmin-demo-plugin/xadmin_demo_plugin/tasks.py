#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""插件周期任务（二开扩展点）：随 app 的 ``tasks.py`` 被 celery autodiscover 自动登记。

``register_as_period_task(module="demo_plugin")`` 声明任务归属的功能模块——
模块被停用时任务不注册（历史注册条目在启动时清理），重新启用自动恢复，
与内置模块的周期任务同口径（模块裁剪第四层）。

任务函数要求：无参数（``args`` / ``kwargs`` 由装饰器声明）；重任务请委托给独立
worker 队列或走 ``TaskExecution`` 进度链路（见 task app 的统一任务中心口径）。
"""

from common.celery.decorator import register_as_period_task
from common.utils import get_logger

logger = get_logger(__name__)


@register_as_period_task(
    crontab="30 4 * * *",
    name="demo_plugin.daily_note_stat",
    description="插件示例任务：每日统计插件便签数量（演示模块裁剪的周期任务层）",
    module="demo_plugin",
)
def daily_note_stat() -> None:
    """示例周期任务：模块停用时不注册；启用时每日 04:30 打一条统计日志。"""
    from xadmin_demo_plugin.models import PluginNote

    logger.info(
        "demo plugin daily note stat: total=%s active=%s",
        PluginNote.objects.count(),
        PluginNote.objects.filter(is_active=True).count(),
    )
