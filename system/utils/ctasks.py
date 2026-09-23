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

from common.storage import clean_storage_cache
from system.models import OperationLog, UploadFile, UserLoginLog
from system.utils.preview import clean_preview_cache

logger = get_task_logger(__name__)


def auto_clean_operation_log(clean_day=None):
    """先归档后清理过期审计日志：操作日志 + 登录日志。

    - 归档：把「整月已超保留期」的日志导出为 ``JSONL.gz``（含 sha256 与清单，幂等）；
    - 清理：边界由**归档水位**驱动（删必已归档；未归档的边界月最多多留一个月）；
    - 保留期：操作日志 ``OPERATION_LOG_RETENTION_DAYS``（错误日志按
      ``OPERATION_LOG_ERROR_RETENTION_DAYS`` 分层留存）、登录日志
      ``LOGIN_LOG_RETENTION_DAYS``（默认 365）；对象保留期置 0 = 该对象不清理；
    - 归档失败时抛错跳过本次清理（保数据优先），任务在 celery 记录中可见失败，
      避免「未归档即删除」的静默数据损失；
    - 同时输出剩余行数，便于监控告警：清理强依赖 celery beat 部署，
      beat 未部署时该任务静默不执行，表会无限增长。
    """
    from system.utils.log_archive import archive_expired, prune_archived

    deleted = 0
    for model_key, model in (("operation", OperationLog), ("login", UserLoginLog)):
        # clean_day 参数（历史签名）只覆盖操作日志保留期；登录日志走自身配置
        override = clean_day if model_key == "operation" else None
        archive_expired(model_key, retention_days=override)
        deleted += prune_archived(model_key, retention_days=override)
        logger.info(f"clean {model_key} log remaining {model.objects.count()}")
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


def auto_clean_preview_cache(keep_days=None):
    """清理预览缓存（孤儿 + 超保留期），保留期取 FILE_PREVIEW_CACHE_KEEP_DAYS。

    与上传文件清理同源纪律：缓存是派生产物，删了可按需重建，
    因此不需要"引用守护"那一层保守判断，只保留"最近使用"淘汰。
    """
    result = clean_preview_cache(keep_days=keep_days)
    # 对象存储后端会为预览/转换把远端对象缓存到本地（MEDIA_ROOT/storage_cache），
    # 同属派生产物，与预览缓存一起按最近使用淘汰
    removed_storage_cache = clean_storage_cache(keep_days=keep_days)
    logger.info(
        f"clean preview cache scanned:{result['scanned']} "
        f"orphan:{result['removed_orphan']} expired:{result['removed_expired']} "
        f"storage_cache:{removed_storage_cache}"
    )
    return result["removed_orphan"] + result["removed_expired"] + removed_storage_cache


def auto_clean_ai_usage(retention_days=None, batch_size=2000):
    """分批清理超保留期的 AI 用量记录：保留期取 MONITOR_RETENTION_DAYS。

    用量账本是观测数据（与监控心跳同口径），过期即失去成本归因价值；
    0/缺省 = 跟随系统配置，配置为 0 表示不清理。
    """
    from common.core.config import SysConfig
    from system.models.ai import AiUsageRecord

    if retention_days is None:
        retention_days = SysConfig.MONITOR_RETENTION_DAYS
    retention_days = int(retention_days or 0)
    if retention_days <= 0:
        return 0
    deadline = timezone.now() - datetime.timedelta(days=retention_days)
    removed = 0
    while True:
        pks = list(AiUsageRecord.objects.filter(created_time__lt=deadline).values_list("pk", flat=True)[:batch_size])
        if not pks:
            break
        removed += AiUsageRecord.objects.filter(pk__in=pks).delete()[0]
    logger.info(f"clean {removed} AI usage records (retention {retention_days} days)")
    return removed
