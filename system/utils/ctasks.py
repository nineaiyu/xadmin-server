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
    for instance in UploadFile.all_objects.filter(created_time__lte=clean_time, is_tmp=True):
        # 临时文件无回收价值，必须物理删除（含底层文件清理），
        # 否则软删除标记会导致磁盘泄漏
        instance.hard_delete()
        _rows_count += 1
    logger.info(f"clean {_rows_count} upload tmp file")


def auto_clean_upload_file(keep_days=None, batch_size=2000):
    """分批清理超过保留期的正式上传文件（FILE_KEEP_DAYS，0 = 不清理）。

    - 只处理非临时文件（临时文件由 auto_clean_tmp_file 按天清理）；
    - 有业务引用的记录整体跳过：删除记录会把业务外键 SET_NULL，造成附件断链；
    - 物理文件删除走 ``UploadFile.file_still_referenced`` 守护：同 md5 / 同路径的
      其他活动记录仍在时只删记录、保留磁盘文件；
    - 跳过的记录在后续轮次不再扫描，避免「整批都被引用」时反复空转。
    """
    from common.core.config import SysConfig

    days = SysConfig.FILE_KEEP_DAYS if keep_days is None else keep_days
    if not days or days <= 0:
        return 0
    deadline = timezone.now() - datetime.timedelta(days=days)
    skipped = set()
    removed = 0
    while True:
        records = list(
            UploadFile.objects.filter(is_tmp=False, created_time__lte=deadline)
            .exclude(pk__in=skipped)
            .order_by("created_time")[:batch_size]
        )
        if not records:
            break
        for record in records:
            if record.has_business_reference():
                skipped.add(record.pk)
                continue
            record.hard_delete()
            removed += 1
    logger.info(f"clean {removed} upload file, keep_days {days}")
    return removed
