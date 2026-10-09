#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""过期文件清理实现体（任务函数壳留在 system.tasks，celery 任务名不变）。

- 临时文件：无回收价值，物理删除（软删除会导致磁盘泄漏）；
- 正式上传文件：分批 + 业务引用守护（引用中的记录整体跳过，磁盘文件
  由 ``UploadFile.file_still_referenced`` 决定去留）；
- 预览缓存：派生产物，孤儿 + 保留期两条回收路径，另含对象存储本地缓存。
"""

import datetime
from typing import Any

from celery.utils.log import get_task_logger
from django.utils import timezone

from common.storage import clean_storage_cache
from file.models import UploadFile
from file.utils.preview import clean_preview_cache

logger = get_task_logger(__name__)


def auto_clean_tmp_file(clean_day: int = 1) -> None:
    clean_time = timezone.now() - datetime.timedelta(days=clean_day)
    _rows_count = 0
    for instance in UploadFile.all_objects.filter(created_time__lte=clean_time, is_tmp=True):
        # 临时文件无回收价值，必须物理删除（含底层文件清理），
        # 否则软删除标记会导致磁盘泄漏
        instance.hard_delete()
        _rows_count += 1
    logger.info(f"clean {_rows_count} upload tmp file")


def auto_clean_upload_file(keep_days: int | None = None, batch_size: int = 2000) -> int:
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
    skipped: set[Any] = set()
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


def auto_clean_preview_cache(keep_days: int | None = None) -> int:
    """清理预览缓存（孤儿 + 超保留期），保留期取 FILE_PREVIEW_CACHE_KEEP_DAYS。

    与上传文件清理同源纪律：缓存是派生产物，删了可按需重建，
    因此不需要"引用守护"那一层保守判断，只保留"最近使用"淘汰。
    """
    result = clean_preview_cache(keep_days=keep_days)
    # 对象存储后端会为预览/转换把远端对象缓存到本地（MEDIA_ROOT/storage_cache），
    # 同属派生产物，与预览缓存一起按最近使用淘汰
    removed_storage_cache: int = clean_storage_cache(keep_days=keep_days)
    logger.info(
        f"clean preview cache scanned:{result['scanned']} "
        f"orphan:{result['removed_orphan']} expired:{result['removed_expired']} "
        f"storage_cache:{removed_storage_cache}"
    )
    return result["removed_orphan"] + result["removed_expired"] + removed_storage_cache
