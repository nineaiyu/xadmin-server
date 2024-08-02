#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : signal_handler
# author : ly_13
# date : 6/29/2023
import logging

from celery.signals import after_setup_logger
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django_celery_results.models import TaskResult

from common.base.utils import remove_file
from common.celery.logger import CeleryThreadTaskFileHandler
from common.celery.utils import get_celery_task_log_path

logger = logging.getLogger(__name__)


@receiver(pre_delete, sender=TaskResult)
def delete_file_handler(sender, **kwargs):
    # 清理任务记录，同时并清理日志文件
    instance = kwargs.get('instance')
    if instance:
        task_id = instance.task_id
        if task_id:
            log_path = get_celery_task_log_path(task_id)
            remove_file(log_path)


# @receiver(worker_ready)
# def start_ai_chat(*args, **kwargs):
#     clean_old_caches()
#     task = robot_ai_chat_job.apply_async(time_limit=24 * 3600 * 31 * 12)
#     logger.error(f"clean cache {task} {task.id}")


@after_setup_logger.connect
def on_after_setup_logger(sender=None, logger=None, loglevel=None, format=None, **kwargs):
    if not logger:
        return
    task_handler = CeleryThreadTaskFileHandler()
    task_handler.setLevel(loglevel)
    formatter = logging.Formatter(format)
    task_handler.setFormatter(formatter)
    logger.addHandler(task_handler)
