#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""AI 平台周期任务（自 system/tasks/__init__.py 与 system/utils/ctasks.py 随域迁出）。"""

from celery import shared_task

from common.celery.decorator import register_as_period_task
from common.utils import get_logger

logger = get_logger(__name__)


@shared_task
@register_as_period_task(crontab="12 3 * * *")
def auto_clean_ai_usage_job():
    """AI 用量账本保留期清理（保留期随 MONITOR_RETENTION_DAYS）。"""
    from ai.utils.ai_usage import auto_clean_ai_usage

    auto_clean_ai_usage()
