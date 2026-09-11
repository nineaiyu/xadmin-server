#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : tasks
# author : ly_13
# date : 6/29/2023

import datetime
from io import BytesIO
from urllib.parse import urlencode

from django.db import models, transaction

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
from server.utils import set_current_request
from system.models.task import TaskExecution
from system.utils.ctasks import (
    auto_clean_operation_log,
    auto_clean_black_token,
    auto_clean_tmp_file,
    auto_clean_upload_file,
    auto_clean_preview_cache,
)

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
@register_as_period_task(crontab="56 2 * * *")
def auto_clean_upload_file_job():
    """清理超过保留期的正式上传文件（FILE_KEEP_DAYS，默认 0 = 不清理）。"""
    auto_clean_upload_file()


@shared_task
@register_as_period_task(crontab="4 3 * * *")
def auto_clean_preview_cache_job():
    """清理预览缓存（孤儿目录 + 超 FILE_PREVIEW_CACHE_KEEP_DAYS 未访问的缓存）。"""
    auto_clean_preview_cache()


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
    while True:
        # 分批处理：避免逐条扫描 + 逐条两条删除，随数据累积单次任务耗时线性上升
        records = list(ExportRecord.objects.filter(created_time__lt=deadline).select_related("file")[:500])
        if not records:
            break
        for record in records:
            upload = record.file
            record.delete()
            if upload:
                # 硬删除才会清理底层文件（UploadFile 为软删除模型）
                upload.hard_delete()
            removed += 1
    logger.info("Clean export record: %s rows, keep_days: %s", removed, keep_days)
    return removed


@shared_task
@register_as_period_task(crontab="58 2 * * *")
def auto_clean_import_record_job():
    """清理超过保留期的异步导入记录、源文件与错误报告（IMPORT_RECORD_KEEP_DAYS，默认 30 天）。"""
    from system.models.import_ import ImportRecord

    from common.core.config import SysConfig  # 局部导入避免循环依赖（config <-> system.services）

    keep_days = SysConfig.IMPORT_RECORD_KEEP_DAYS
    deadline = timezone.now() - datetime.timedelta(days=keep_days)
    removed = 0
    while True:
        # 分批处理：避免逐条扫描 + 逐条两条删除，随数据累积单次任务耗时线性上升
        records = list(
            ImportRecord.objects.filter(created_time__lt=deadline).select_related("source_file", "error_report")[:500]
        )
        if not records:
            break
        for record in records:
            source_file, error_report = record.source_file, record.error_report
            record.delete()
            for upload in (source_file, error_report):
                if upload:
                    # 硬删除才会清理底层文件（UploadFile 为软删除模型）
                    upload.hard_delete()
            removed += 1
    logger.info("Clean import record: %s rows, keep_days: %s", removed, keep_days)
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


@shared_task
@register_as_period_task(crontab="22 3 * * *")
def auto_clean_pat_job():
    """清理个人访问令牌：过期超 30 天的凭证，以及停用且 30 天未更新的凭证。"""
    from system.models.token import PersonalAccessToken

    deadline = timezone.now() - datetime.timedelta(days=30)
    removed = PersonalAccessToken.objects.filter(
        models.Q(expired_at__lt=deadline) | models.Q(is_active=False, updated_time__lt=deadline)
    ).delete()[0]
    if removed:
        logger.info("Clean personal access tokens: %s rows", removed)
    return removed


@shared_task
@register_as_period_task(crontab="42 3 * * *")
def auto_expire_approval_job():
    """敏感操作审批单超时（APPROVAL_PENDING_TIMEOUT，默认 3 天）置 EXPIRED。"""
    from system.utils.approval import expire_pending_approvals

    count = expire_pending_approvals()
    if count:
        logger.info("Expire pending approvals: %s rows", count)
    return count


@shared_task
@register_as_period_task(crontab="0 9 * * *")
def auto_remind_approval_job():
    """待审批超时提醒（APPROVAL_REMIND_HOURS，默认 24h）：每日 09:00 对未处理且未提醒过的单补发一次。"""
    from system.utils.approval import remind_pending_approvals

    count = remind_pending_approvals()
    if count:
        logger.info("Remind pending approvals: %s rows", count)
    return count


@shared_task
@register_as_period_task(crontab="52 3 * * *")
def auto_clean_approval_job():
    """清理超过保留期的审批单（APPROVAL_KEEP_DAYS，默认 180 天，分批删）。"""
    from system.utils.approval import clean_expired_approvals

    removed = clean_expired_approvals()
    if removed:
        logger.info("Clean approval requests: %s rows", removed)
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


def _save_progress(record, percent):
    """运行中进度落库（0-100）：进度条数据源，终态由任务结束分支覆盖。"""
    percent = max(0, min(100, int(percent)))
    if record.progress != percent:
        record.progress = percent
        record.save(update_fields=["progress", "updated_time"])


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
    # 导出为视图整体重放，无法逐行上报，仅里程碑粒度：RUNNING 10 → 计数完成 30 →
    # 内容渲染完成 80 → SUCCESS 100
    record.progress = 10
    record.save(update_fields=["status", "progress", "updated_time"])
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
        _save_progress(record, 30)

        response = view_cls.as_view({"get": "export_data"})(request)
        response.render()
        if response.status_code != 200:
            raise ValueError(f"export view returned status {response.status_code}")
        content = response.content
        _save_progress(record, 80)

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
        record.progress = 100
        record.error = None
        record.save(update_fields=["file", "rows", "status", "progress", "error", "updated_time"])
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


class _ImportAborted(Exception):
    """失败率超限中止：触发外层事务回滚（成功行一并撤销）。"""


def _upload_import_error_report(record, user, column_titles, errors):
    """失败行错误报告落 UploadFile(is_tmp=True)，返回实例。"""
    import os
    import tempfile

    from django.core.files.base import ContentFile

    from system.models.upload import UploadFile
    from system.utils.import_report import build_error_report

    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        build_error_report(tmp_path, column_titles, errors)
        with open(tmp_path, "rb") as fp:
            content = fp.read()
    finally:
        os.remove(tmp_path)
    filename = f"{record.name}_errors.xlsx"
    upload = UploadFile(
        filename=filename,
        filesize=len(content),
        mime_type=EXPORT_MIME_TYPES["xlsx"],
        is_tmp=True,
        is_upload=False,
        creator=user,
    )
    upload.filepath.save(filename, ContentFile(content), save=False)
    upload.save()
    return upload


def _import_row(view, action_type, row):
    """单行导入：校验 + 写入，失败抛异常由调用方 savepoint 回滚。"""
    from rest_framework.exceptions import ValidationError

    if action_type == "update":
        instance = view.filter_queryset(view.get_queryset()).filter(pk=row.get("pk")).first()
        if instance is None:
            raise ValidationError(_("Object not found: {}").format(row.get("pk")))
        serializer = view.get_serializer(instance, data=row, partial=True)
    else:
        serializer = view.get_serializer(data=row)
    serializer.is_valid(raise_exception=True)
    if action_type == "update":
        view.perform_update(serializer)
    else:
        view.perform_create(serializer)


@shared_task(bind=True, verbose_name=_("Async import data"))
def async_import_data_task(self, record_id, view_path, user_pk):
    """异步执行数据导入：任务内解析源文件，逐行 savepoint 导入并生成失败行报告。

    - 记录状态在任务内推进（PENDING → RUNNING → SUCCESS/FAILURE）；同 pk 的
      TaskExecution 在同步执行（apply/EAGER）时补建，执行历史/增量日志双环境可用；
    - 逐行独立 savepoint：单行失败回滚该行继续下一行；失败率超
      IMPORT_FAIL_RATE_LIMIT（默认 0.5，0=不限制）时中止并回滚全部成功行；
    - 校验/写入复用目标视图的 serializer（字段权限/联动校验同源），
      threadlocal 请求注入保证 creator 信号正常赋值。
    """
    from rest_framework.request import Request

    from common.core.config import SysConfig
    from common.notifications import ImportDataMessage
    from system.models.import_ import ImportRecord
    from system.models.task import TaskExecution
    from system.models.user import UserInfo
    from system.utils.import_progress import clear_import_progress, set_import_progress

    record = ImportRecord.objects.filter(pk=record_id).first()
    if record is None:
        logger.warning("Import record not found: %s", record_id)
        return 0
    TaskExecution.objects.get_or_create(
        pk=record_id,
        defaults={"name": "system.tasks.async_import_data_task", "args": [record.name], "kwargs": record.params or {}},
    )
    user = UserInfo.objects.filter(pk=user_pk).first() if user_pk else None
    record.status = ImportRecord.Status.RUNNING
    record.save(update_fields=["status", "updated_time"])
    start_time, state = local_now_display(), True
    total = success_rows = 0
    errors, column_titles = [], []
    aborted, abort_reason = False, None
    try:
        if not record.source_file or not record.source_file.filepath:
            raise ValueError(_("Import source file not found"))
        source_path = record.source_file.filepath.path

        view_cls = import_string(view_path)
        view = view_cls()
        environ = {
            "REQUEST_METHOD": "POST",
            "SCRIPT_NAME": "",
            "PATH_INFO": record.path or "/",
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
            request._force_auth_user = user
        drf_request = Request(request, parsers=[])
        if user:
            drf_request.user = user
        view.request = drf_request
        view.action = "import_data"
        view.kwargs = {}
        # get_serializer_context 依赖 format_kwarg（导出重放装配同款坑）
        view.format_kwarg = None
        set_current_request(drf_request)

        # 行数据在 action 内已由文件解析器解析并序列化为 JSON（与同步导入同一条解析链）
        import json

        with open(source_path, "r", encoding="utf-8") as fp:
            rows = json.load(fp)
        column_titles = (record.params or {}).get("column_titles") or []
        total = len(rows)
        if rows:
            # 自关联依赖拓扑排序（与同步导入 import_data 同口径，父行先建）
            from common.core.utils import has_self_fields, topological_sort

            self_field = has_self_fields(view.get_queryset().model, rows[0].keys())
            if self_field:
                rows = topological_sort(rows, parent=self_field)

        fail_rate_limit = SysConfig.IMPORT_FAIL_RATE_LIMIT
        # 运行期进度走缓存通道：本循环包在外层事务里，事务提交前其他连接读不到
        # 库内进度（见 system/utils/import_progress 模块说明），因此不写库、只写缓存
        last_percent = -1
        try:
            with transaction.atomic():
                for idx, row in enumerate(rows, start=1):
                    try:
                        with transaction.atomic():
                            _import_row(view, record.action, row)
                        success_rows += 1
                    except Exception as exc:
                        errors.append(
                            {
                                "row": idx,
                                "values": {str(k): row.get(k) for k in row},
                                "error": str(exc)[:500],
                            }
                        )
                        if fail_rate_limit and fail_rate_limit > 0 and failed_rate(errors, total) > fail_rate_limit:
                            aborted = True
                            abort_reason = _("Aborted: failure rate exceeds limit ({}/{} rows failed)").format(
                                len(errors), total
                            )
                            break
                    # 分批上报进度（1% 粒度），供下载中心进度条展示
                    percent = int(idx / max(total, 1) * 100)
                    if percent != last_percent:
                        last_percent = percent
                        set_import_progress(record.pk, percent)
                if aborted:
                    # 外层事务回滚：已写入的成功行一并撤销
                    raise _ImportAborted(abort_reason)
        except _ImportAborted:
            pass
    except Exception as exc:
        state = False
        record.status = ImportRecord.Status.FAILURE
        record.error = str(exc)[:2000]
        record.total = record.total or total
        record.save(update_fields=["status", "error", "total", "updated_time"])
        clear_import_progress(record.pk)
        logger.exception("async import failed: %s", record_id)
        raise
    finally:
        set_current_request(None)

    # 走到这里：解析成功（含失败率中止回滚场景），推进终态与报告
    try:
        record.total = total
        record.success_rows = 0 if aborted else success_rows
        record.failed_rows = len(errors)
        if aborted:
            record.status = ImportRecord.Status.FAILURE
            record.error = abort_reason
        else:
            record.status = ImportRecord.Status.SUCCESS
            record.progress = 100
            record.error = None
        if errors and column_titles:
            record.error_report = _upload_import_error_report(record, user, column_titles, errors)
        record.save(
            update_fields=[
                "total",
                "success_rows",
                "failed_rows",
                "status",
                "progress",
                "error",
                "error_report",
                "updated_time",
            ]
        )
        logger.info(
            "async import done: total %s, success %s, failed %s, aborted %s",
            total,
            record.success_rows,
            record.failed_rows,
            aborted,
        )
    except Exception:
        logger.exception("async import finalize failed: %s", record_id)
        raise
    finally:
        # 终态已落库，清掉运行期缓存进度（序列化器 RUNNING 时才读缓存）
        clear_import_progress(record.pk)
        if user:
            try:
                ImportDataMessage(
                    user,
                    {
                        "task_name": record.name,
                        "view_doc": record.module or record.name,
                        "state": state,
                        "status": _("Operation successful") if state else _("Operation failed"),
                        "tasks": [
                            {
                                "task_id": str(record.pk),
                                "start_time": start_time,
                                "end_time": local_now_display(),
                                "result": record.error
                                or _("Imported {} rows, {} failed").format(record.success_rows, record.failed_rows),
                            }
                        ],
                    },
                ).publish()
            except Exception:
                logger.warning("Send import data message failed", exc_info=True)
    return record.success_rows


def failed_rate(errors, total):
    """当前失败率（total 防零）。"""
    return len(errors) / max(total, 1)
