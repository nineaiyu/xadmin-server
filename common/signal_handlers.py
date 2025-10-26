#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : signal_handler
# author : ly_13
# date : 6/29/2023
import logging
import re
from collections import defaultdict

from celery import signature
from celery.signals import worker_ready, worker_shutdown, after_setup_logger
from django.conf import settings
from django.core.cache import cache
from django.core.signals import request_finished
from django.db import connection
from django.db.models.signals import pre_delete, pre_save
from django.dispatch import receiver
from django_celery_beat.models import PeriodicTask
from django_celery_results.models import TaskResult

from common.base.utils import remove_file
from common.celery.decorator import get_after_app_ready_tasks, get_after_app_shutdown_clean_tasks
from common.celery.logger import CeleryThreadTaskFileHandler
from common.celery.utils import get_celery_task_log_path
from common.signals import django_ready
from common.utils import get_logger
from server.utils import get_current_request

logger = get_logger(__name__)
safe_str = lambda x: x

pattern = re.compile(r'FROM `(\w+)`')


@worker_ready.connect
def on_app_ready(sender=None, headers=None, **kwargs):
    if cache.get("CELERY_APP_READY", 0) == 1:
        return
    cache.set("CELERY_APP_READY", 1, 10)
    tasks = get_after_app_ready_tasks()
    logger.debug("Work ready signal recv")
    logger.debug("Start need start task: [{}]".format(", ".join(tasks)))
    for task in tasks:
        periodic_task = PeriodicTask.objects.filter(task=task).first()
        if periodic_task and not periodic_task.enabled:
            logger.debug("Periodic task [{}] is disabled!".format(task))
            continue
        signature(task).delay()


@worker_shutdown.connect
def after_app_shutdown_periodic_tasks(sender=None, **kwargs):
    if cache.get("CELERY_APP_SHUTDOWN", 0) == 1:
        return
    cache.set("CELERY_APP_SHUTDOWN", 1, 10)
    tasks = get_after_app_shutdown_clean_tasks()
    logger.debug("Worker shutdown signal recv")
    logger.debug("Clean period tasks: [{}]".format(', '.join(tasks)))
    PeriodicTask.objects.filter(name__in=tasks).delete()


@receiver(pre_delete, sender=TaskResult)
def delete_file_handler(sender, **kwargs):
    # 清理任务记录，同时并清理日志文件
    instance = kwargs.get('instance')
    if instance:
        task_id = instance.task_id
        if task_id:
            log_path = get_celery_task_log_path(task_id)
            remove_file(log_path)


@after_setup_logger.connect
def on_after_setup_logger(sender=None, logger=None, loglevel=None, format=None, **kwargs):
    if not logger:
        return
    task_handler = CeleryThreadTaskFileHandler()
    task_handler.setLevel(loglevel)
    formatter = logging.Formatter(format)
    task_handler.setFormatter(formatter)
    logger.addHandler(task_handler)


class Counter:
    def __init__(self):
        self.counter = 0
        self.time = 0

    def __gt__(self, other):
        return self.counter > other.counter

    def __lt__(self, other):
        return self.counter < other.counter

    def __eq__(self, other):
        return self.counter == other.counter


def on_request_finished_logging_db_query(sender, **kwargs):
    queries = connection.queries
    counters = defaultdict(Counter)
    table_queries = defaultdict(list)
    for query in queries:
        if not query['sql'] or not query['sql'].startswith('SELECT'):
            continue
        tables = pattern.findall(query['sql'])
        table_name = ''.join(tables)
        time = query['time']
        counters[table_name].counter += 1
        counters[table_name].time += float(time)
        counters['total'].counter += 1
        counters['total'].time += float(time)
        table_queries[table_name].append(query)

    counters = sorted(counters.items(), key=lambda x: x[1])
    if not counters:
        return

    method = 'GET'
    path = '/Unknown'
    current_request = get_current_request()
    if current_request:
        method = current_request.method
        path = current_request.get_full_path()

    print(">>>. [{}] {}".format(method, path))

    for name, counter in counters:
        logger.debug("Query {:3} times using {:.2f}s {}".format(
            counter.counter, counter.time, name)
        )


def _get_request_user():
    current_request = get_current_request()
    if current_request and current_request.user and current_request.user.is_authenticated:
        return current_request.user


@receiver(pre_save)
def on_create_set_creator(sender, instance=None, **kwargs):
    if getattr(instance, '_ignore_auto_creator', False):
        return
    if not hasattr(instance, 'creator') or instance.creator:
        return
    creator = _get_request_user()
    if creator:
        instance.creator = creator
        if hasattr(instance, 'dept_belong'):
            instance.dept_belong = creator.dept


@receiver(pre_save)
def on_update_set_modifier(sender, instance=None, **kwargs):
    if getattr(instance, '_ignore_auto_modifier', False):
        return
    if hasattr(instance, 'modifier'):
        modifier = _get_request_user()
        if modifier:
            instance.modifier = modifier


if settings.DEBUG_DEV:
    request_finished.connect(on_request_finished_logging_db_query)


@receiver(django_ready)
def clear_response_cache(sender, **kwargs):
    cache.delete_pattern('magic_cache_response_*')
