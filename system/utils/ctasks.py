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


def auto_clean_operation_log(clean_day=30 * 6):
    return OperationLog.remove_expired(clean_day)


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
