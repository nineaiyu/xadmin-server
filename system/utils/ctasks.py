#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : ctasks
# author : ly_13
# date : 6/29/2023

import datetime

from celery.utils.log import get_task_logger
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from system.models import OperationLog, UploadFile

logger = get_task_logger(__name__)


def auto_clean_operation_log(clean_day=None):
    """分批清理过期操作日志（保留期默认取系统配置 OPERATION_LOG_RETENTION_DAYS）。

    同时输出剩余行数，便于监控告警：清理强依赖 celery beat 部署，
    beat 未部署时该任务静默不执行，表会无限增长。
    """
    deleted = OperationLog.remove_expired(clean_day)
    remaining = OperationLog.objects.count()
    logger.info(f"clean {deleted} operation log. remaining {remaining}")
    return deleted


def auto_clean_black_token(clean_day=1):
    clean_time = timezone.now() - datetime.timedelta(days=clean_day)
    deleted, _rows_count = OutstandingToken.objects.filter(expires_at__lte=clean_time).delete()
    logger.info(f"clean {_rows_count} black token {deleted}")


def auto_clean_tmp_file(clean_day=1):
    clean_time = timezone.now() - datetime.timedelta(days=clean_day)
    _rows_count = 0
    for instance in UploadFile.objects.filter(created_time__lte=clean_time, is_tmp=True):
        if instance.delete():
            _rows_count += 1
    logger.info(f"clean {_rows_count} upload tmp file")
