#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""task 域信号接收器（task.apps.ready 导入挂载）。"""

from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from django_celery_beat.models import PeriodicTask

from task.models.task import PeriodicTaskOwner, TaskExecution


@receiver(pre_delete, sender=TaskExecution)
def delete_task_execution_log_handler(sender, **kwargs):
    # 执行历史删除（含批量删除）时联动清理落盘日志文件
    instance = kwargs.get("instance")
    if instance:
        from common.base.utils import remove_file
        from common.celery.utils import get_celery_task_log_path

        remove_file(get_celery_task_log_path(str(instance.pk)))


@receiver(post_save, sender=PeriodicTask)
def record_periodic_task_owner_handler(sender, instance, created, **kwargs):
    """PeriodicTask 新建时兜底落配置者归属（side 表 PeriodicTaskOwner）。

    周期任务的创建入口分散：任务管理页创建/克隆（task 域视图）、启动期系统
    注册（common/celery/utils.py，受框架层依赖方向约束不宜反向 import task）、
    种子命令与 Admin 等——显式写入难以收敛且新入口易漏，统一在 save 信号兜底：

    - 仅新建（created=True）落行；更新路径（含 beat 启停簿记）不落行，归属
      保持「谁配置归谁」；record_for 幂等，重复触发/并发落行不覆盖既有归属；
    - creator 依赖全局审计信号回填：页面创建/克隆为操作者，beat/worker/命令行
      等无请求上下文即为空（系统注册，无人工归属）。
    """
    if not created:
        return
    PeriodicTaskOwner.record_for(instance)
