#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : utils
# author : ly_13
# date : 6/29/2023
import logging
import os
from datetime import datetime, timedelta

from django.conf import settings

logger = logging.getLogger(__name__)

# celery 日志完成之后，写入的魔法字符，作为结束标记
CELERY_LOG_MAGIC_MARK = b'\x00\x00\x00\x00\x00'


def make_dirs(name, mode=0o755, exist_ok=False):
    """ 默认权限设置为 0o755 """
    return os.makedirs(name, mode=mode, exist_ok=exist_ok)


def get_task_log_path(base_path, task_id, level=0):
    task_id = str(task_id)
    rel_path = os.path.join(*task_id[:level], task_id + '.log')
    path = os.path.join(base_path, rel_path)
    make_dirs(os.path.dirname(path), exist_ok=True)
    return path


def get_celery_task_log_path(task_id):
    return get_task_log_path(settings.CELERY_LOG_DIR, task_id)


def eta_second(second):
    return datetime.utcfromtimestamp(datetime.now().timestamp()) + timedelta(seconds=second)
