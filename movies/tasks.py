#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : tasks
# author : ly_13
# date : 11/17/2023

import logging
from datetime import datetime, timedelta

from celery import shared_task

from common.base.magic import MagicCacheData
from movies.models import AliyunDrive
from movies.utils.storage import get_aliyun_drive
from server.celery import app

logger = logging.getLogger(__file__)


def eta_second(second):
    ctime = datetime.now()
    utc_ctime = datetime.utcfromtimestamp(ctime.timestamp())
    time_delay = timedelta(seconds=second)
    return utc_ctime + time_delay


@shared_task
def sync_drive_size(batch_pks):
    for drive_obj in AliyunDrive.objects.filter(pk__in=batch_pks).all():
        try:
            ali_obj = get_aliyun_drive(drive_obj)
            default_drive_obj = ali_obj.get_default_drive()
            drive_obj.total_size = default_drive_obj.total_size
            drive_obj.used_size = default_drive_obj.used_size
            drive_obj.active = True
            drive_obj.save(update_fields=['total_size', 'used_size', 'active', 'updated_time'])
            logger.info(f'{drive_obj} update size success')
        except Exception as e:
            logger.warning(f'{drive_obj} update drive size failed:{e}')


@MagicCacheData.make_cache(timeout=60 * 10, key_func=lambda *args: args[0])
def delay_sync_drive_size(drive_pk):
    c_task = sync_drive_size.apply_async(args=([drive_pk],), eta=eta_second(60 * 10))
    logger.info(f'{drive_pk} delay exec {c_task}')


@app.task
def batch_sync_drive_size(batch=100):
    """
    :param batch:
    :return:
    主要用户阿里网盘token刷新，并获取磁盘空间大小，每天凌晨2点执行
    """
    drive_pks = AliyunDrive.objects.filter(active=True, enable=True).values('pk').all()
    for index in range(int(len(drive_pks) / batch) + 1):
        batch_pks = drive_pks[index * batch:(index + 1) * batch]
        if batch_pks:
            sync_drive_size.apply_async(args=(batch_pks,))

# @shared_task
# def refresh_home_cache():
#     cache_response.invalid_cache('HomeView_get')
#
#
# @MagicCacheData.make_cache(timeout=60)
# def delay_refresh_home_cache():
#     c_task = refresh_home_cache.apply_async(eta=eta_second(60))
#     logger.info(f'delay_refresh_home_cache exec {c_task}')
