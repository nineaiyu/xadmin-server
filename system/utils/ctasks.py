#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : ctasks
# author : ly_13
# date : 6/29/2023

import datetime
import logging

from captcha.models import CaptchaStore
from celery.utils.log import get_task_logger
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from system.models import OperationLog

logger = logging.getLogger(__name__)


def auto_clean_operation_log(clean_day=30 * 6):
    return OperationLog.remove_expired(clean_day)


def auto_clean_expired_captcha():
    CaptchaStore.remove_expired()


def auto_clean_black_token(clean_day=1):
    from django.utils import timezone
    logger = get_task_logger(__name__)
    clean_time = timezone.now() - datetime.timedelta(days=clean_day)
    OutstandingToken.objects.filter(expires_at__lte=clean_time).delete()
