#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LDAP 同步周期任务入口。

注册范式与 system/tasks.py 一致：``@shared_task`` + ``@register_as_period_task``
启动时 upsert 到 django_celery_beat；任务内先查 ``LDAP_SYNC_ENABLED``，管理页可
随时停用；手动触发复用周期任务管理页 run 动作（自动获得 TaskExecution 历史）。
"""

from celery import shared_task

from common.celery.decorator import register_as_period_task
from common.utils import get_logger

logger = get_logger(__name__)


@shared_task
@register_as_period_task(crontab="17 * * * *", description="LDAP 目录同步（用户/部门/状态）", module="ldap")
def sync_ldap_directory_job():
    from system.ldap.sync import run_ldap_sync

    summary = run_ldap_sync()
    logger.info("LDAP sync finished: %s", summary)
    return summary
