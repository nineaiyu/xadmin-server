#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : tasks
# author : ly_13
# date : 9/15/2024
from celery import shared_task

from captcha.models import CaptchaStore
from common.celery.decorator import register_as_period_task


@shared_task
@register_as_period_task(crontab='12 2 * * *')
def auto_clean_expired_captcha_job():
    CaptchaStore.remove_expired()
