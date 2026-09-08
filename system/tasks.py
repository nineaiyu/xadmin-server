#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : tasks
# author : ly_13
# date : 6/29/2023

import datetime

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from django_celery_results.models import TaskResult

from common.base.utils import remove_file
from common.celery.decorator import register_as_period_task
from common.celery.utils import get_celery_task_log_path
from common.utils import get_logger
from system.models.task import TaskExecution
from system.utils.ctasks import auto_clean_operation_log, auto_clean_black_token, auto_clean_tmp_file

logger = get_logger(__name__)


@shared_task
@register_as_period_task(crontab='2 2 * * *')
def auto_clean_operation_job():
    # 保留期读取系统配置 OPERATION_LOG_RETENTION_DAYS（默认 180 天），不再硬编码
    auto_clean_operation_log()


@shared_task
@register_as_period_task(crontab='22 2 * * *')
def auto_clean_black_token_job():
    auto_clean_black_token(clean_day=7)


@shared_task
@register_as_period_task(crontab='32 2 * * *')
def auto_clean_tmp_file_job():
    auto_clean_tmp_file(clean_day=7)


@shared_task
@register_as_period_task(crontab='42 2 * * *')
def auto_clean_task_execution_job():
    """清理超过保留期的执行历史与日志文件（TaskResult 删除联动清日志）。"""
    keep_days = getattr(settings, "TASK_EXECUTION_KEEP_DAYS", 30)
    deadline = timezone.now() - datetime.timedelta(days=keep_days)
    removed = 0
    while True:
        executions = list(
            TaskExecution.objects.filter(created_time__lt=deadline)
            .values_list("pk", flat=True)[:500]
        )
        if not executions:
            break
        for pk in executions:
            remove_file(get_celery_task_log_path(str(pk)))
        removed += TaskExecution.objects.filter(pk__in=executions).delete()[0]
    removed += TaskResult.objects.filter(date_done__lt=deadline).delete()[0]
    logger.info("Clean task execution history: %s rows", removed)
    return removed
