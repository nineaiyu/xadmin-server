#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : utils
# author : ly_13
# date : 6/29/2023
import json
import os
from datetime import datetime, timedelta, UTC

from django.conf import settings
from django.db.utils import ProgrammingError, OperationalError
from django.utils import timezone
from django_celery_beat.models import IntervalSchedule, CrontabSchedule, PeriodicTask, PeriodicTasks

from common.utils import get_logger
from common.utils.timezone import local_now

logger = get_logger(__name__)

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
    return datetime.fromtimestamp(datetime.now().timestamp(), UTC) + timedelta(seconds=second)


def create_or_update_celery_periodic_tasks(tasks):
    """
    :param tasks: {
        'add-every-monday-morning': {
            'task': 'tasks.add' # A registered celery task,
            'interval': 30,
            'crontab': "30 7 * * *",
            'args': (16, 16),
            'kwargs': {},
            'enabled': False,
            'description': ''
        },
    }
    :return:
    """
    # Todo: check task valid, task and callback must be a celery task
    for name, detail in tasks.items():
        interval = None
        crontab = None
        last_run_at = None

        try:
            IntervalSchedule.objects.all().count()
        except (ProgrammingError, OperationalError):
            return None

        if isinstance(detail.get("interval"), int):
            kwargs = dict(
                every=detail['interval'],
                period=IntervalSchedule.SECONDS,
            )
            # 不能使用 get_or_create，因为可能会有多个
            interval = IntervalSchedule.objects.filter(**kwargs).first()
            if interval is None:
                interval = IntervalSchedule.objects.create(**kwargs)
            last_run_at = local_now()
        elif isinstance(detail.get("crontab"), str):
            try:
                minute, hour, day, month, week = detail["crontab"].split()
            except ValueError:
                logger.error("crontab is not valid")
                return
            kwargs = dict(
                minute=minute, hour=hour, day_of_week=week,
                day_of_month=day, month_of_year=month, timezone=timezone.get_current_timezone()
            )
            crontab = CrontabSchedule.objects.filter(**kwargs).first()
            if crontab is None:
                crontab = CrontabSchedule.objects.create(**kwargs)
        else:
            logger.error("Schedule is not valid")
            return

        defaults = dict(
            interval=interval,
            crontab=crontab,
            name=name,
            task=detail['task'],
            args=json.dumps(detail.get('args', [])),
            kwargs=json.dumps(detail.get('kwargs', {})),
            description=detail.get('description') or '',
            last_run_at=last_run_at,
        )
        enabled = detail.get('enabled')
        if enabled is not None:
            defaults["enabled"] = enabled
        task = PeriodicTask.objects.update_or_create(
            defaults=defaults, name=name,
        )
        PeriodicTasks.update_changed()
        return task


def disable_celery_periodic_task(task_name):
    from django_celery_beat.models import PeriodicTask
    PeriodicTask.objects.filter(name=task_name).update(enabled=False)
    PeriodicTasks.update_changed()


def delete_celery_periodic_task(task_name):
    from django_celery_beat.models import PeriodicTask
    PeriodicTask.objects.filter(name=task_name).delete()
    PeriodicTasks.update_changed()


def get_celery_periodic_task(task_name):
    from django_celery_beat.models import PeriodicTask
    task = PeriodicTask.objects.filter(name=task_name).first()
    return task
