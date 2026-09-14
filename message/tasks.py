#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天室周期任务（二期）：聊天历史自动清理。

celery autodiscover 会导入各安装应用的 ``tasks`` 模块，``message`` 为顶层应用，
本模块随应用自动注册到 django_celery_beat，无需在 system/tasks.py 显式引入。
"""

from celery import shared_task

from common.celery.decorator import register_as_period_task
from common.utils import get_logger
from message import chat as chat_service

logger = get_logger(__name__)


@shared_task
@register_as_period_task(crontab="23 3 * * *")
def clean_chat_history_job():
    """清理超过 CHAT_HISTORY_DAYS 的聊天消息（0 = 不清理，默认关闭）。"""
    removed = chat_service.clean_expired_history()
    logger.info(f"clean {removed} chat history message")
    return removed
