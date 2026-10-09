#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""任务域清理实现体（周期任务壳的委托体，经 task.services 消费）。

保留期均读系统配置：TASK_EXECUTION_KEEP_DAYS / EXPORT_FILE_KEEP_DAYS /
IMPORT_RECORD_KEEP_DAYS。任务名与注册面（system.tasks.* 周期任务壳）零变化。
"""

import datetime
from typing import Any

from django.conf import settings
from django.utils import timezone
from django_celery_results.models import TaskResult

from common.base.utils import remove_file
from common.celery.utils import get_celery_task_log_path
from common.utils import get_logger
from task.models.export import ExportRecord
from task.models.import_ import ImportRecord
from task.models.task import TaskExecution

logger = get_logger(__name__)


def clean_task_executions() -> Any:
    """清理超过保留期的执行历史与日志文件（TaskResult 删除联动清日志）。"""
    keep_days = getattr(settings, "TASK_EXECUTION_KEEP_DAYS", 30)
    deadline = timezone.now() - datetime.timedelta(days=keep_days)
    removed = 0
    while True:
        executions = list(TaskExecution.objects.filter(created_time__lt=deadline).values_list("pk", flat=True)[:500])
        if not executions:
            break
        for pk in executions:
            remove_file(get_celery_task_log_path(str(pk)))
        removed += TaskExecution.objects.filter(pk__in=executions).delete()[0]
    removed += TaskResult.objects.filter(date_done__lt=deadline).delete()[0]
    logger.info("Clean task execution history: %s rows", removed)
    return removed


def clean_export_records() -> Any:
    """清理超过保留期的异步导出记录与产物文件（EXPORT_FILE_KEEP_DAYS，默认 7 天）。"""
    from common.core.config import SysConfig  # 局部导入避免循环依赖（config <-> services 契约层）

    keep_days = SysConfig.EXPORT_FILE_KEEP_DAYS
    deadline = timezone.now() - datetime.timedelta(days=keep_days)
    removed = 0
    while True:
        # 分批处理：避免逐条扫描 + 逐条两条删除，随数据累积单次任务耗时线性上升
        records = list(ExportRecord.objects.filter(created_time__lt=deadline).select_related("file")[:500])
        if not records:
            break
        for record in records:
            upload = record.file
            record.delete()
            if upload:
                # 硬删除才会清理底层文件（UploadFile 为软删除模型）
                upload.hard_delete()
            removed += 1
    logger.info("Clean export record: %s rows, keep_days: %s", removed, keep_days)
    return removed


def clean_import_records() -> Any:
    """清理超过保留期的异步导入记录、源文件与错误报告（IMPORT_RECORD_KEEP_DAYS，默认 30 天）。"""
    from common.core.config import SysConfig  # 局部导入避免循环依赖（config <-> services 契约层）

    keep_days = SysConfig.IMPORT_RECORD_KEEP_DAYS
    deadline = timezone.now() - datetime.timedelta(days=keep_days)
    removed = 0
    while True:
        # 分批处理：避免逐条扫描 + 逐条两条删除，随数据累积单次任务耗时线性上升
        records = list(
            ImportRecord.objects.filter(created_time__lt=deadline).select_related("source_file", "error_report")[:500]
        )
        if not records:
            break
        for record in records:
            source_file, error_report = record.source_file, record.error_report
            record.delete()
            for upload in (source_file, error_report):
                if upload:
                    # 硬删除才会清理底层文件（UploadFile 为软删除模型）
                    upload.hard_delete()
            removed += 1
    logger.info("Clean import record: %s rows, keep_days: %s", removed, keep_days)
    return removed
