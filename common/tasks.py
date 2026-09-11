#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : tasks
# author : ly_13
# date : 7/30/2024
import datetime
import os
from io import BytesIO

from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.core.handlers.wsgi import WSGIRequest
from django.core.mail import send_mail, EmailMultiAlternatives, get_connection
from django.utils import timezone, translation
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _
from django_celery_beat.models import PeriodicTask

from common.cache.lock import ReentrantLock
from common.cache.redis import CacheList
from common.celery.decorator import register_as_period_task, after_app_ready_start
from common.core.utils import get_doc_first_line
from common.celery.utils import (
    delete_celery_periodic_task,
    disable_celery_periodic_task,
    get_celery_periodic_task,
    create_or_update_celery_periodic_tasks,
)
from common.models import Monitor
from common.notifications import ServerPerformanceCheckUtil, ImportDataMessage, BatchDeleteDataMessage
from common.utils.timezone import local_now_display
from server.celery import app

logger = get_task_logger(__name__)


@shared_task(verbose_name=_("Send email"))
def send_mail_async(*args, **kwargs):
    """Using celery to send email async

    You can use it as django send_mail function

    Example:
    send_mail_sync.delay(subject, message, from_mail, recipient_list, fail_silently=False, html_message=None)

    Also, you can ignore the from_mail, unlike django send_mail, from_email is not a required args:

    Example:
    send_mail_sync.delay(subject, message, recipient_list, fail_silently=False, html_message=None)
    """
    if len(args) == 3:
        args = list(args)
        args[0] = f"{settings.EMAIL_SUBJECT_PREFIX or ''} {args[0]}"
        from_email = settings.EMAIL_FROM or settings.EMAIL_HOST_USER
        args.insert(2, from_email)

    args = tuple(args)

    subject = args[0] if len(args) > 0 else kwargs.get("subject")
    recipient_list = args[3] if len(args) > 3 else kwargs.get("recipient_list")
    logger.info(f"send_mail_async called with subject={subject}, recipients={recipient_list}")

    try:
        return send_mail(connection=get_connection(), *args, **kwargs)
    except Exception as e:
        logger.error("Sending mail error: {}".format(e))


@shared_task(verbose_name=_("Send email attachment"))
def send_mail_attachment_async(subject, message, recipient_list, attachment_list=None):
    if attachment_list is None:
        attachment_list = []
    from_email = settings.EMAIL_FROM or settings.EMAIL_HOST_USER
    subject = f"{settings.EMAIL_SUBJECT_PREFIX or ''} {subject}"
    email = EmailMultiAlternatives(
        subject=subject,
        body=message,
        from_email=from_email,
        to=recipient_list,
        connection=get_connection(),
    )
    for attachment in attachment_list:
        email.attach_file(attachment)
        os.remove(attachment)
    try:
        return email.send()
    except Exception as e:
        logger.error("Sending mail attachment error: {}".format(e))


@shared_task(verbose_name=_("Periodic delete monitor"))
@register_as_period_task(interval=3600)
@after_app_ready_start
def auto_clean_monitor_logs():
    """心跳历史保留期清理（MONITOR_RETENTION_DAYS，默认 30 天），按 pk 分批删除。

    心跳 30s 一条长期落库，单批一次性 DELETE 在大保留期下会长时间锁表，
    沿用 OperationLog.remove_expired 的分批范式。
    """
    from common.core.config import SysConfig

    retention_days = SysConfig.MONITOR_RETENTION_DAYS
    if retention_days <= 0:
        return 0
    old_times = timezone.now() - datetime.timedelta(days=retention_days)
    removed = 0
    batch_size = 2000
    while True:
        pks = list(Monitor.objects.filter(created_time__lt=old_times).values_list("pk", flat=True)[:batch_size])
        if not pks:
            break
        removed += Monitor.objects.filter(pk__in=pks).delete()[0]
    logger.info("Clean monitor heartbeat history: %s rows (retention %s days)", removed, retention_days)
    return removed


@shared_task(
    verbose_name=_("Clear celery periodic tasks"),
    description=_("At system startup, clean up celery tasks that no longer exist"),
)
@after_app_ready_start
def clean_celery_periodic_tasks():
    logger.info("Start clean celery periodic tasks.")
    register_tasks = PeriodicTask.objects.all()
    for task in register_tasks:
        if task.task in app.tasks:
            continue

        task_name = task.name
        logger.info("Start clean task: {}".format(task_name))
        disable_celery_periodic_task(task_name)
        delete_celery_periodic_task(task_name)
        task = get_celery_periodic_task(task_name)
        if task is None:
            logger.info("Clean task success: {}".format(task_name))
        else:
            logger.info("Clean task failure: {}".format(task))


@shared_task(
    verbose_name=_("Create or update periodic tasks"),
    description=_(
        """With version iterations, new tasks may be added, or task names and execution times may 
        be modified. Therefore, upon system startup, tasks will be registered or the parameters 
        of scheduled tasks will be updated"""
    ),
)
@after_app_ready_start
def create_or_update_registered_periodic_tasks():
    from .celery.decorator import get_register_period_tasks

    for task in get_register_period_tasks():
        create_or_update_celery_periodic_tasks(task)


@shared_task(
    verbose_name=_("Periodic check service performance"),
    description=_(
        """Check every hour whether each component is offline and whether the CPU, memory, 
        and disk usage exceed the thresholds, and send an alert message to the administrator"""
    ),
)
@register_as_period_task(interval=60)
def check_server_performance_period():
    ServerPerformanceCheckUtil().check_and_publish()


@shared_task(verbose_name=_("Run background task view set"))
def background_task_view_set_job(view: str, meta: dict, data: str, action_map: dict):
    cache = CacheList(f"view_task_{meta.get('task_id').split('_')[0]}", timeout=3600 * 24)
    task_info = {
        "start_time": local_now_display(),
        "task_id": meta.get("task_id"),
        "task_index": meta.get("task_index"),
    }
    view_func = import_string(view)
    b_data = data.encode("utf-8")
    meta["wsgi.input"] = BytesIO(b_data)
    meta["CONTENT_TYPE"] = "application/json"
    meta["CONTENT_LENGTH"] = len(b_data)
    request = WSGIRequest(meta)
    language = translation.get_language_from_request(request)
    translation.activate(language)
    request.LANGUAGE_CODE = translation.get_language()
    result = view_func.as_view(action_map)(request, task=False)
    # detail 可能是 gettext 惰性代理（如兜底 500 文案），不物化会让 cache.push 的
    # json.dumps 崩溃，进而丢掉整批分片结果
    task_info["result"] = str(result.data.get("detail", result.data))
    task_info["end_time"] = local_now_display()
    task_info["status"] = result.data.get("code") == 1000
    cache.push(task_info)
    # 分片结果汇总判定：持锁时长随分片数浮动，看门狗自动续期防锁先于业务失效
    with ReentrantLock(f"view_task_summary_{meta.get('task_id').split('_')[0]}", timeout=180):
        if cache.len() and cache.len() == meta["task_count"]:
            task_results = cache.get_all()
            cache.delete()
            state = all([task["status"] for task in task_results])
            task_info = {
                "task_name": view,
                "view_doc": get_doc_first_line(view_func.__doc__),
                "state": state,
                "status": _("Operation successful") if state else _("Operation failed"),
                "tasks": sorted(task_results, key=lambda task: task["task_index"]),
            }
            match meta["action"]:
                case "import_data":
                    ImportDataMessage(getattr(request, "user"), task_info).publish()
                case "batch_destroy":
                    BatchDeleteDataMessage(getattr(request, "user"), task_info).publish()

    return task_info


@shared_task(
    verbose_name=_("Purge soft deleted data"),
    description=_("Physically purge recycle bin data older than RECYCLE_BIN_RETENTION_DAYS"),
)
@register_as_period_task(interval=86400)
@after_app_ready_start
def purge_soft_deleted():
    """物理清除回收站中超过保留期的软删除数据（含底层文件/级联清理）。"""
    from django.apps import apps

    from common.core.models import SoftDeleteModel

    retention_days = getattr(settings, "RECYCLE_BIN_RETENTION_DAYS", 30)
    cutoff = timezone.now() - datetime.timedelta(days=retention_days)
    total = 0
    for model in apps.get_models():
        if not issubclass(model, SoftDeleteModel):
            continue
        count = 0
        for instance in model.all_objects.filter(deleted_at__lt=cutoff).iterator():
            instance.hard_delete()
            count += 1
        if count:
            logger.info(f"purge {count} soft deleted data. model: {model._meta.label}")
        total += count
    logger.info(f"purge soft deleted data done. retention_days: {retention_days}, total: {total}")
    return total
