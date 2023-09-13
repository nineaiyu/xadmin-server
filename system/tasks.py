#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : tasks
# author : ly_13
# date : 6/29/2023

import logging

from celery import shared_task

from system.utils.ctasks import auto_clean_operation_log, auto_clean_expired_captcha, auto_clean_black_token, \
    auto_clean_tmp_file

logger = logging.getLogger(__name__)


@shared_task
def auto_clean_operation_job():
    auto_clean_operation_log(clean_day=30 * 6)


@shared_task
def auto_clean_expired_captcha_job():
    auto_clean_expired_captcha()


@shared_task
def auto_clean_black_token_job():
    auto_clean_black_token(clean_day=7)


@shared_task
def auto_clean_tmp_file_job():
    auto_clean_tmp_file(clean_day=7)
