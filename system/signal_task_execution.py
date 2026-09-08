#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : signal_task_execution
# author : ly_13
# date : 9/8/2026
"""celery 全局信号 → TaskExecution 自动记账。
- after_task_publish（发布进程）：get_or_create PENDING 记录；beat 定时投递的
  message headers 带 periodic_task_name（django_celery_beat/schedulers.py:89），
  据此回填关联；手动执行场景记录已预创建，不覆盖 creator；
- task_prerun / task_postrun（worker 进程）：推进 RUNNING / 终态；
- task_revoked：REVOKED。
handler 均用幂等 update/get_or_create，重复触发无副作用。
"""

from celery.signals import (
    after_task_publish,
    task_postrun,
    task_prerun,
    task_revoked,
    worker_ready,
)
from django.core.cache import cache
from django.utils import timezone
from django_celery_beat.models import PeriodicTask, PeriodicTasks

from common.utils import get_logger
from server.celery import app
from server.utils import get_current_request
from system.models.task import TaskExecution

logger = get_logger(__name__)


def _request_user():
    request = get_current_request()
    if request and request.user and request.user.is_authenticated:
        return request.user
    return None


@after_task_publish.connect
def task_execution_on_publish(sender=None, headers=None, body=None, **kwargs):
    headers = headers or {}
    task_id = headers.get("id")
    if not task_id:
        return
    args, exec_kwargs = (body or ((), {}))[:2]
    defaults = {
        "name": headers.get("task") or sender or "",
        "args": list(args or []),
        "kwargs": dict(exec_kwargs or {}),
        "creator": _request_user(),
    }
    periodic_task_name = headers.get("periodic_task_name")
    if periodic_task_name:
        defaults["periodic_task"] = PeriodicTask.objects.filter(name=periodic_task_name).first()
    try:
        TaskExecution.objects.get_or_create(pk=task_id, defaults=defaults)
    except Exception:  # 记账失败不能影响任务投递
        logger.exception("TaskExecution on_publish failed: %s", task_id)


@task_prerun.connect
def task_execution_on_start(task_id=None, task=None, **kwargs):
    if not task_id:
        return
    TaskExecution.objects.filter(pk=task_id).update(status=TaskExecution.Status.RUNNING, date_start=timezone.now())


@task_postrun.connect
def task_execution_on_finish(task_id=None, state=None, **kwargs):
    if not task_id:
        return
    status = state or TaskExecution.Status.SUCCESS
    if status not in TaskExecution.Status.values:
        status = TaskExecution.Status.SUCCESS
    TaskExecution.objects.filter(pk=task_id, date_finished__isnull=True).update(
        status=status, date_finished=timezone.now()
    )


@task_revoked.connect
def task_execution_on_revoked(request=None, terminated=None, expired=None, **kwargs):
    task_id = getattr(request, "id", None)
    if not task_id:
        return
    TaskExecution.objects.filter(pk=task_id).update(status=TaskExecution.Status.REVOKED, date_finished=timezone.now())


@worker_ready.connect
def clean_orphan_periodic_tasks(sender=None, **kwargs):
    """worker 就绪后清理 task 不在注册表中的孤儿 PeriodicTask。

    代码重命名/删除任务后，其历史定时配置会成为 beat 无法执行的死配置。
    cache 守卫：多 worker 并发启动只清一次。
    """
    if cache.get("CLEAN_ORPHAN_PERIODIC_TASKS", 0) == 1:
        return
    cache.set("CLEAN_ORPHAN_PERIODIC_TASKS", 1, 30)
    registered = set(app.tasks.keys())
    orphans = PeriodicTask.objects.exclude(task__in=registered)
    count = orphans.count()
    if count:
        logger.warning("Clean orphan periodic tasks: %s", list(orphans.values_list("name", flat=True)))
        orphans.delete()
        PeriodicTasks.update_changed()
