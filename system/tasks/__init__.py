#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : tasks
# author : ly_13
# date : 6/29/2023
"""系统异步任务入口（周期任务 + Office 转换 + 导入导出任务壳）。

约定：celery 任务名 = 函数 ``__module__`` + 函数名，因此**任务函数必须留在本模块**
（``system.tasks.<name>``），重实现体拆入私有子模块（``_export`` / ``_import``），
避免任务改名导致既有周期任务登记、TaskExecution 记录与告警路由失配。
"""

import datetime

from celery import shared_task
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_celery_results.models import TaskResult

from common.base.utils import remove_file
from common.celery.decorator import register_as_period_task
from common.celery.utils import get_celery_task_log_path
from common.utils import get_logger
from system.models.task import TaskExecution
from system.utils.ctasks import (
    auto_clean_ai_usage,
    auto_clean_black_token,
    auto_clean_operation_log,
    auto_clean_preview_cache,
    auto_clean_tmp_file,
    auto_clean_upload_file,
)

logger = get_logger(__name__)

# LDAP 同步周期任务：celery autodiscover 只导入 <app>.tasks，
# 子包任务必须在此显式引入才会注册到 django_celery_beat
from system.analysis_tasks import dispatch_scheduled_reports as _dispatch_scheduled_reports  # noqa: F401,E402
from system.ldap.tasks import sync_ldap_directory_job as _sync_ldap_directory_job  # noqa: F401,E402
from system.webhook_tasks import deliver_webhook as _deliver_webhook  # noqa: F401,E402


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
@register_as_period_task(crontab="0 8 * * *")
def account_expiry_job():
    """账号有效期维护：到期前 N 天提醒（站内信 + 邮件），到期自动停用。"""
    from system.utils.account_expiry import disable_expired_accounts, notify_expiring_accounts

    notified = notify_expiring_accounts()
    disabled = disable_expired_accounts()
    return {"notified": notified, "disabled": disabled}


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
@register_as_period_task(crontab="12 3 * * *")
def auto_clean_ai_usage_job():
    """AI 用量账本保留期清理（保留期随 MONITOR_RETENTION_DAYS）。"""
    auto_clean_ai_usage()


@shared_task
@register_as_period_task(crontab="52 2 * * *")
def auto_clean_export_record_job():
    """清理超过保留期的异步导出记录与产物文件（EXPORT_FILE_KEEP_DAYS，默认 7 天）。"""
    from common.core.config import SysConfig  # 局部导入避免循环依赖（config <-> system.services）
    from system.models.export import ExportRecord

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
    from common.core.config import SysConfig  # 局部导入避免循环依赖（config <-> system.services）
    from system.models.import_ import ImportRecord

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
def convert_office_preview_task(upload_pk):
    """Office 文件转 PDF 预览：走 heavy 队列，产物落预览缓存。

    队列归属由 `CELERY_TASK_ROUTES` 按任务名路由；结束后释放转换锁，
    让后续请求（转换失败的场景）可以重新触发。
    """
    from django.core.cache import cache

    from system.models import UploadFile
    from system.utils.preview import convert_office_to_pdf

    try:
        upload = UploadFile.all_objects.filter(pk=upload_pk).first()
        if upload is None:
            return False
        return bool(convert_office_to_pdf(upload))
    finally:
        cache.delete(f"office_converting_{upload_pk}")


@shared_task(bind=True, verbose_name=_("Async export data"))
def async_export_data_task(self, record_id, view_path, query_params, user_pk):
    """异步执行数据导出：重放 export_data 视图，产物落 UploadFile 供下载中心取用。

    记录状态在任务内推进（PENDING → RUNNING → SUCCESS/FAILURE）；同 pk 的
    TaskExecution 由 after_task_publish/prerun/postrun 信号自动记账，
    因此执行历史页与增量日志零成本复用。实现体见 ``system.tasks._export``。
    """
    from ._export import run_async_export

    return run_async_export(record_id, view_path, query_params, user_pk)


@shared_task
@register_as_period_task(crontab="23 4 * * *")
def scan_account_risk_job():
    """账号安全风险巡检：弱项巡检一次，产出/刷新待处置风险清单。"""
    from system.utils.account_risk import scan_account_risks

    return scan_account_risks()


@shared_task
@register_as_period_task(crontab="12 3 * * *")
def auto_clean_file_access_log_job():
    """清理超过保留期的文件访问日志（FILE_ACCESS_LOG_KEEP_DAYS，0 = 不清理）。"""
    from system.utils.file_audit import clean_expired_file_access_logs

    return clean_expired_file_access_logs()


@shared_task(bind=True, verbose_name=_("Async import data"))
def async_import_data_task(self, record_id, view_path, user_pk):
    """异步执行数据导入：任务内解析源文件，逐行 savepoint 导入并生成失败行报告。

    - 记录状态在任务内推进（PENDING → RUNNING → SUCCESS/FAILURE）；同 pk 的
      TaskExecution 在同步执行（apply/EAGER）时补建，执行历史/增量日志双环境可用；
    - 逐行独立 savepoint：单行失败回滚该行继续下一行；失败率超
      IMPORT_FAIL_RATE_LIMIT（默认 0.5，0=不限制）时中止并回滚全部成功行；
    - 校验/写入复用目标视图的 serializer（字段权限/联动校验同源），
      threadlocal 请求注入保证 creator 信号正常赋值。
    实现体见 ``system.tasks._import``。
    """
    from ._import import run_async_import

    return run_async_import(record_id, view_path, user_pk)
