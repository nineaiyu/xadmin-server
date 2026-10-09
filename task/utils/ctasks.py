#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : ctasks
# author : ly_13
# date : 6/29/2023
import datetime
from typing import Any

from celery.utils.log import get_task_logger
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

logger = get_task_logger(__name__)


def auto_clean_black_token(clean_day: Any = 1) -> None:
    clean_time = timezone.now() - datetime.timedelta(days=clean_day)
    deleted, _rows_count = OutstandingToken.objects.filter(expires_at__lte=clean_time).delete()
    logger.info(f"clean {_rows_count} black token {deleted}")
