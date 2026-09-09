#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : tasks
# author : ly_13
# date : 6/29/2023

import datetime
from io import BytesIO
from urllib.parse import urlencode

from celery import shared_task
from django.conf import settings
from django.core.handlers.wsgi import WSGIRequest
from django.utils import timezone, translation
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _
from django_celery_results.models import TaskResult

from common.base.utils import remove_file
from common.celery.decorator import register_as_period_task
from common.celery.utils import get_celery_task_log_path
from common.utils import get_logger
from common.utils.timezone import local_now_display
from system.models.task import TaskExecution
from system.utils.ctasks import auto_clean_operation_log, auto_clean_black_token, auto_clean_tmp_file

logger = get_logger(__name__)

# 导出产物 MIME：下载中心按记录后缀回写 Content-Type
EXPORT_MIME_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
}


@shared_task
@register_as_period_task(crontab="2 2 * * *")
def auto_clean_operation_job():
    # 保留期读取系统配置 OPERATION_LOG_RETENTION_DAYS（默认 180 天），不再硬编码
    auto_clean_operation_log()


@shared_task
@register_as_period_task(crontab="22 2 * * *")
def auto_clean_black_token_job():
    auto_clean_black_token(clean_day=7)


@shared_task
@register_as_period_task(crontab="32 2 * * *")
def auto_clean_tmp_file_job():
    auto_clean_tmp_file(clean_day=7)


@shared_task
@register_as_period_task(crontab="42 2 * * *")
def auto_clean_task_execution_job():
    """清理超过保留期的执行历史与日志文件（TaskResult 删除联动清日志）。"""
    keep_days = getattr(settings, "TASK_EXECUTION_KEEP_DAYS", 30)
    deadline = timezone.now() - datetime.timedelta(days=keep_days)
    removed = 0
    while True:
        executions = list(TaskExecution.objects.filter(created_time__lt=deadline).values_list("pk", flat=True)[:500])
        if not executions:
            break
        for pk in executions:
            remove_file(get_celery_task_log_path(str(pk)))
        removed += TaskExecution.objects.filter(pk__in=executions).delete()[0]
    removed += TaskResult.objects.filter(date_done__lt=deadline).delete()[0]
    logger.info("Clean task execution history: %s rows", removed)
    return removed


@shared_task
@register_as_period_task(crontab="52 2 * * *")
def auto_clean_export_record_job():
    """清理超过保留期的异步导出记录与产物文件（EXPORT_FILE_KEEP_DAYS，默认 7 天）。"""
    from system.models.export import ExportRecord

    from common.core.config import SysConfig  # 局部导入避免循环依赖（config <-> system.services）

    keep_days = SysConfig.EXPORT_FILE_KEEP_DAYS
    deadline = timezone.now() - datetime.timedelta(days=keep_days)
    removed = 0
    for record in ExportRecord.objects.filter(created_time__lt=deadline).iterator():
        upload = record.file
        record.delete()
        if upload:
            # 硬删除才会清理底层文件（UploadFile 为软删除模型）
            upload.hard_delete()
        removed += 1
    logger.info("Clean export record: %s rows, keep_days: %s", removed, keep_days)
    return removed


@shared_task
@register_as_period_task(interval=300)
def auto_expire_user_session_job():
    """HTTP 会话活跃窗口（SESSION_ONLINE_TIMEOUT，默认 300s）外置离线。

    WS 会话不在此列：其在线判定由 channel 存活决定，优雅断开由 WS logout
    钩子标记，异常残留由 auto_clean_user_session_job 按保留期回收。
    """
    from system.utils.session import expire_stale_sessions

    count = expire_stale_sessions()
    if count:
        logger.info("Expire stale user sessions: %s rows", count)
    return count


@shared_task
@register_as_period_task(crontab="12 3 * * *")
def auto_clean_user_session_job():
    """删除超过保留期的会话记录（USER_SESSION_RETENTION_DAYS，默认 30 天）。"""
    from system.utils.session import clean_expired_sessions

    removed = clean_expired_sessions()
    if removed:
        logger.info("Clean user session: %s rows", removed)
    return removed


def build_export_request(record, query_params, user):
    """构造用于重放 export_data 的原始请求。

    直接注入提交者身份（DRF ForcedAuthentication），不在任务里重新签发 access
    token：任务排队可能超过 access token 寿命（默认 1h），重新签发的令牌会因
    过期导致重放认证失败。注入 user 后视图内的菜单/数据/字段三层权限与
    filterset 过滤仍按提交者身份生效。
    """
    environ = {
        "REQUEST_METHOD": "GET",
        "SCRIPT_NAME": "",
        "PATH_INFO": record.path or "/",
        "QUERY_STRING": urlencode(query_params or {}, doseq=True),
        "SERVER_NAME": "xadmin",
        "SERVER_PORT": "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "HTTP_HOST": "xadmin",
        "wsgi.input": BytesIO(b""),
        "wsgi.errors": BytesIO(),
        "wsgi.url_scheme": "http",
    }
    request = WSGIRequest(environ)
    if user:
        # DRF 初始化 Request 时检测到 _force_auth_user，改用 ForcedAuthentication，
        # 跳过 JWT 解析直接以该用户身份执行后续权限链
        request._force_auth_user = user
    return request


@shared_task(bind=True, verbose_name=_("Async export data"))
def async_export_data_task(self, record_id, view_path, query_params, user_pk):
    """异步执行数据导出：重放 export_data 视图，产物落 UploadFile 供下载中心取用。

    记录状态在任务内推进（PENDING → RUNNING → SUCCESS/FAILURE）；同 pk 的
    TaskExecution 由 after_task_publish/prerun/postrun 信号自动记账，
    因此执行历史页与增量日志零成本复用。
    """
    from django.core.files.base import ContentFile

    from common.notifications import ExportDataMessage
    from system.models.export import ExportRecord
    from system.models.task import TaskExecution
    from system.models.upload import UploadFile
    from system.models.user import UserInfo

    record = ExportRecord.objects.filter(pk=record_id).first()
    if record is None:
        logger.warning("Export record not found: %s", record_id)
        return 0
    # 真实投递由 after_task_publish 自动记账；同步执行（apply/EAGER）不发该信号，此处补齐，
    # 保证执行历史页与增量日志在两种环境下都可用
    TaskExecution.objects.get_or_create(
        pk=record_id,
        defaults={"name": "system.tasks.async_export_data_task", "args": [record.name], "kwargs": query_params or {}},
    )
    user = UserInfo.objects.filter(pk=user_pk).first() if user_pk else None
    record.status = ExportRecord.Status.RUNNING
    record.save(update_fields=["status", "updated_time"])
    start_time, total, state = local_now_display(), 0, True
    try:
        request = build_export_request(record, query_params, user)
        translation.activate(translation.get_language_from_request(request))
        view_cls = import_string(view_path)

        # 行数走与导出同一套 filterset + 数据权限链，避免为计数做一次全量序列化
        from rest_framework.request import Request

        probe = view_cls()
        probe.action = "export_data"
        probe.kwargs = {}
        probe.format_kwarg = None
        probe.request = Request(request, parsers=[])
        if user:
            probe.request.user = user
        total = probe.filter_queryset(probe.get_queryset()).count()
        logger.info("async export %s total rows: %s", view_path, total)

        response = view_cls.as_view({"get": "export_data"})(request)
        response.render()
        if response.status_code != 200:
            raise ValueError(f"export view returned status {response.status_code}")
        content = response.content

        filename = f"{record.name}.{record.file_format}"
        upload = UploadFile(
            filename=filename,
            filesize=len(content),
            mime_type=EXPORT_MIME_TYPES.get(record.file_format, "application/octet-stream"),
            is_tmp=True,
            is_upload=False,
            creator=user,
        )
        upload.filepath.save(filename, ContentFile(content), save=False)
        upload.save()
        record.file = upload
        record.rows = min(total, getattr(settings, "EXPORT_MAX_LIMIT", total))
        record.status = ExportRecord.Status.SUCCESS
        record.error = None
        record.save(update_fields=["file", "rows", "status", "error", "updated_time"])
        logger.info("async export done: %s bytes, rows: %s", len(content), record.rows)
    except Exception as exc:
        state = False
        record.status = ExportRecord.Status.FAILURE
        record.error = str(exc)[:2000]
        record.save(update_fields=["status", "error", "updated_time"])
        logger.exception("async export failed: %s", record_id)
        # 继续抛出，交给 celery 标记任务失败并触发 task_failure 告警
        raise
    finally:
        if user:
            try:
                ExportDataMessage(
                    user,
                    {
                        "task_name": record.name,
                        "state": state,
                        "status": _("Operation successful") if state else _("Operation failed"),
                        "tasks": [
                            {
                                "task_id": str(record.pk),
                                "start_time": start_time,
                                "end_time": local_now_display(),
                                "result": record.error or _("Exported {} rows").format(record.rows),
                            }
                        ],
                    },
                ).publish()
            except Exception:
                logger.warning("Send export data message failed", exc_info=True)
    return record.rows
