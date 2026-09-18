#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : tasks
# author : ly_13
# date : 7/30/2024
import datetime
import os

from celery import Task, shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives, get_connection, send_mail
from django.utils import timezone, translation
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _
from django_celery_beat.models import PeriodicTask

from common.cache.lock import ReentrantLock
from common.cache.redis import CacheList
from common.celery.decorator import after_app_ready_start, register_as_period_task
from common.celery.utils import (
    create_or_update_celery_periodic_tasks,
    delete_celery_periodic_task,
    disable_celery_periodic_task,
    get_celery_periodic_task,
)
from common.core.task_request import build_task_request
from common.core.utils import get_doc_first_line
from common.models import Monitor
from common.notifications import BatchDeleteDataMessage, ImportDataMessage, ServerPerformanceCheckUtil
from common.utils.timezone import local_now_display
from server.celery import app
from server.utils import set_current_request

logger = get_task_logger(__name__)


# 邮件发送重试参数（P3）：SMTP 属外部 IO，瞬时失败（连接超时/限流）不再直接丢。
# 任务路径失败按指数退避重试；同步直接调用路径（通知渠道 publish 同步分支）
# 保持「记录错误并返回 None」的既有语义，不抛异常打断业务。
MAIL_MAX_RETRIES = 3
MAIL_RETRY_BACKOFF_MAX = 600


def _strip_task_self(args):
    """剥离 bind=True 注入的 Task 实例首参，返回 (task_self, 业务参数)。

    无论 ``.delay()`` 还是同步直接调用，celery 都会把 Task 实例作为首参传入
    （run 为绑定方法），这里统一剥离，保证两种调用语义一致。
    """
    if args and isinstance(args[0], Task):
        return args[0], args[1:]
    return None, args


@shared_task(bind=True, acks_late=True, verbose_name=_("Send email"))
def send_mail_async(*args, **kwargs):
    """Using celery to send email async

    You can use it as django send_mail function

    Example:
    send_mail_sync.delay(subject, message, from_mail, recipient_list, fail_silently=False, html_message=None)

    Also, you can ignore the from_mail, unlike django send_mail, from_email is not a required args:

    Example:
    send_mail_sync.delay(subject, message, recipient_list, fail_silently=False, html_message=None)
    """
    task_self, args = _strip_task_self(args)
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
        return send_mail(*args, connection=get_connection(), **kwargs)
    except Exception as e:
        direct = task_self is None or getattr(task_self.request, "called_directly", False)
        if not direct and task_self.request.retries < MAIL_MAX_RETRIES:
            countdown = min(60 * (2**task_self.request.retries), MAIL_RETRY_BACKOFF_MAX)
            logger.warning(f"Sending mail failed, retry in {countdown}s: {e}")
            raise task_self.retry(exc=e, countdown=countdown) from e
        logger.error(f"Sending mail error: {e}")


@shared_task(bind=True, acks_late=True, verbose_name=_("Send email attachment"))
def send_mail_attachment_async(*args, **kwargs):
    task_self, args = _strip_task_self(args)
    subject = args[0] if len(args) > 0 else kwargs.get("subject")
    message = args[1] if len(args) > 1 else kwargs.get("message")
    recipient_list = args[2] if len(args) > 2 else kwargs.get("recipient_list")
    attachment_list = args[3] if len(args) > 3 else kwargs.get("attachment_list")
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
    try:
        result = email.send()
    except Exception as e:
        direct = task_self is None or getattr(task_self.request, "called_directly", False)
        if not direct and task_self.request.retries < MAIL_MAX_RETRIES:
            countdown = min(60 * (2**task_self.request.retries), MAIL_RETRY_BACKOFF_MAX)
            logger.warning(f"Sending mail attachment failed, retry in {countdown}s: {e}")
            raise task_self.retry(exc=e, countdown=countdown) from e
        logger.error(f"Sending mail attachment error: {e}")
        return None
    # 临时附件仅在发送成功后删除：失败重试时附件仍需存在（旧实现先删后发，重试必然失败）
    for attachment in attachment_list:
        try:
            os.remove(attachment)
        except OSError:
            logger.warning("Remove mail attachment failed: %s", attachment)
    return result


@shared_task(verbose_name=_("Periodic delete monitor"))
@register_as_period_task(interval=3600, module="ops")
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
        logger.info(f"Start clean task: {task_name}")
        disable_celery_periodic_task(task_name)
        delete_celery_periodic_task(task_name)
        task = get_celery_periodic_task(task_name)
        if task is None:
            logger.info(f"Clean task success: {task_name}")
        else:
            logger.info(f"Clean task failure: {task}")


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
    from .core.modules import is_module_enabled

    for task in get_register_period_tasks():
        # 功能模块裁剪：停用模块的周期任务不注册；已注册的历史条目一并清理
        # （重新启用模块后随本函数自动重建，无需人工干预）
        for name, detail in task.items():
            if not is_module_enabled(detail.get("module")):
                logger.info(f"Skip periodic task of disabled module: {name}")
                delete_celery_periodic_task(name)
                continue
            create_or_update_celery_periodic_tasks({name: detail})


@shared_task(
    verbose_name=_("Periodic check service performance"),
    description=_(
        """Check every hour whether each component is offline and whether the CPU, memory, 
        and disk usage exceed the thresholds, and send an alert message to the administrator"""
    ),
)
@register_as_period_task(interval=60, module="ops")
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
    # 显式请求上下文（不再重放 WSGIRequest）：分片任务由 meta["user_pk"] 携带提交者身份，
    # 逐分片构造独立请求（字段权限的关联对象 memo 按分片隔离）；
    # 五个契约的清单与守护测试见 common/core/task_request.py
    request_user = get_user_model().objects.filter(pk=meta["user_pk"]).first() if meta.get("user_pk") else None
    request = build_task_request(
        method=meta.get("REQUEST_METHOD", "POST"),
        path=meta.get("PATH_INFO", "/"),
        query_params=meta.get("QUERY_STRING", ""),
        body=data.encode("utf-8"),
        user=request_user,
    )
    language = translation.get_language_from_request(request)
    translation.activate(language)
    request.LANGUAGE_CODE = translation.get_language()
    # 契约 5：thread-local 请求（creator 信号赋值 + 操作审计 request_uuid），出口处清理
    set_current_request(request)
    try:
        result = view_func.as_view(action_map)(request, task=False)
    finally:
        set_current_request(None)
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
            # 通知对象：优先显式身份（meta.user_pk 解析）；缺省回落视图内绑定的请求身份
            notify_user = request_user if request_user is not None else getattr(request, "user", None)
            if notify_user is None:
                logger.warning("batch task %s finished without submitter identity; notification skipped", view)
            else:
                match meta["action"]:
                    case "import_data":
                        ImportDataMessage(notify_user, task_info).publish()
                    case "batch_destroy":
                        BatchDeleteDataMessage(notify_user, task_info).publish()

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
