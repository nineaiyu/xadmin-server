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

from common.cache.redis import CacheList
from common.celery.decorator import register_as_period_task, after_app_ready_start
from common.celery.utils import delete_celery_periodic_task, disable_celery_periodic_task, get_celery_periodic_task, \
    create_or_update_celery_periodic_tasks
from common.models import Monitor
from common.notifications import ServerPerformanceCheckUtil, ImportDataMessage, BatchDeleteDataMessage
from common.utils.timezone import local_now_display
from server.celery import app

logger = get_task_logger(__name__)


@shared_task(verbose_name=_("Send email"))
def send_mail_async(*args, **kwargs):
    """ Using celery to send email async

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

    subject = args[0] if len(args) > 0 else kwargs.get('subject')
    recipient_list = args[3] if len(args) > 3 else kwargs.get('recipient_list')
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


@shared_task(verbose_name=_('Periodic delete monitor'))
@register_as_period_task(interval=3600)
@after_app_ready_start
def auto_clean_monitor_logs():
    old_times = timezone.now() - datetime.timedelta(days=30)
    Monitor.objects.filter(created_time__lt=old_times).delete()


@shared_task(
    verbose_name=_('Clear celery periodic tasks'),
    description=_("At system startup, clean up celery tasks that no longer exist")
)
@after_app_ready_start
def clean_celery_periodic_tasks():
    logger.info('Start clean celery periodic tasks.')
    register_tasks = PeriodicTask.objects.all()
    for task in register_tasks:
        if task.task in app.tasks:
            continue

        task_name = task.name
        logger.info('Start clean task: {}'.format(task_name))
        disable_celery_periodic_task(task_name)
        delete_celery_periodic_task(task_name)
        task = get_celery_periodic_task(task_name)
        if task is None:
            logger.info('Clean task success: {}'.format(task_name))
        else:
            logger.info('Clean task failure: {}'.format(task))


@shared_task(
    verbose_name=_('Create or update periodic tasks'),
    description=_(
        """With version iterations, new tasks may be added, or task names and execution times may 
        be modified. Therefore, upon system startup, tasks will be registered or the parameters 
        of scheduled tasks will be updated"""
    )
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
    )
)
@register_as_period_task(interval=60)
def check_server_performance_period():
    ServerPerformanceCheckUtil().check_and_publish()


@shared_task(verbose_name=_("Run background task view set"))
def background_task_view_set_job(view: str, meta: dict, data: str, action_map: dict):
    cache = CacheList(f"view_task_{meta.get("task_id").split("_")[0]}", timeout=3600 * 24)
    task_info = {
        "start_time": local_now_display(),
        "task_id": meta.get("task_id"),
        "task_index": meta.get("task_index")
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
    task_info["result"] = result.data.get("detail", result.data)
    task_info["end_time"] = local_now_display()
    task_info["status"] = result.data.get("code") == 1000
    cache.push(task_info)
    with cache.lock(timeout=180):
        if cache.len() and cache.len() == meta["task_count"]:
            task_results = cache.get_all()
            cache.delete()
            state = all([task["status"] for task in task_results])
            task_info = {
                "task_name": view,
                "view_doc": view_func.__doc__,
                "state": state,
                "status": _("Operation successful") if state else _("Operation failed"),
                "tasks": sorted(task_results, key=lambda task: task["task_index"])
            }
            match meta["action"]:
                case "import_data":
                    ImportDataMessage(getattr(request, "user"), task_info).publish()
                case "batch_destroy":
                    BatchDeleteDataMessage(getattr(request, "user"), task_info).publish()

    return task_info
