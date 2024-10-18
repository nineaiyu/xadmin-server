#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : tasks
# author : ly_13
# date : 6/29/2023

from celery import shared_task

from common.celery.decorator import register_as_period_task
from common.utils import get_logger
from system.utils.ctasks import auto_clean_operation_log, auto_clean_black_token, auto_clean_tmp_file

logger = get_logger(__name__)


@shared_task
@register_as_period_task(crontab='2 2 * * *')
def auto_clean_operation_job():
    auto_clean_operation_log(clean_day=30 * 6)


@shared_task
@register_as_period_task(crontab='22 2 * * *')
def auto_clean_black_token_job():
    auto_clean_black_token(clean_day=7)


@shared_task
@register_as_period_task(crontab='32 2 * * *')
def auto_clean_tmp_file_job():
    auto_clean_tmp_file(clean_day=7)
