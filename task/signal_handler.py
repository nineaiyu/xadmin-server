#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""task 域信号接收器（task.apps.ready 导入挂载）。"""

from django.db.models.signals import pre_delete
from django.dispatch import receiver

from task.models.task import TaskExecution


@receiver(pre_delete, sender=TaskExecution)
def delete_task_execution_log_handler(sender, **kwargs):
    # 执行历史删除（含批量删除）时联动清理落盘日志文件
    instance = kwargs.get("instance")
    if instance:
        from common.base.utils import remove_file
        from common.celery.utils import get_celery_task_log_path

        remove_file(get_celery_task_log_path(str(instance.pk)))
