#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""定时报表任务：调度分发 + 执行渲染 + 邮件送达。

- 分发器每小时跑一次（crontab "5 * * * *"），命中 frequency/send_time/weekday
  的 active 报表派发执行；执行与分发解耦（长渲染不阻塞扫描）；
- 执行以**创建者**权限上下文运行数据集（menu 上下文为空 ⇒ 仅未绑菜单的全局
  授权生效，fail-closed 语义不变）；
- 产物复用下载中心：派发方预创建 ExportRecord（pk == celery task_id 契约），
  xlsx 渲染后落 UploadFile 并挂 record；邮件携带附件，失败仅记 error 不回滚产物。
"""

import io
import json
from datetime import datetime
from datetime import time as dt_time
from uuid import UUID

from celery import shared_task
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.celery.decorator import register_as_period_task
from common.utils import get_logger

logger = get_logger(__name__)

EXPORT_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def report_due(report, now=None) -> bool:
    """调度命中判定：cron_expression 优先；否则 frequency × send_time(× weekday)。now 仅供测试注入。"""
    if (getattr(report, "cron_expression", "") or "").strip():
        return _cron_due(report.cron_expression, now)
    now = now or timezone.localtime()
    if f"{now.hour:02d}:{now.minute:02d}" != report.send_time:
        return False
    if report.frequency == "daily":
        return True
    if report.frequency == "weekly":
        return now.weekday() == int(report.weekday)
    # monthly：每月第一天命中
    return now.day == 1


def _cron_due(expression: str, now=None) -> bool:
    """cron 表达式命中判定（分钟级）：非法表达式视为不命中（fail-closed）。"""
    from croniter import croniter

    expression = (expression or "").strip()
    if not croniter.is_valid(expression):
        return False
    now = now or timezone.localtime()
    return bool(croniter.match(expression, now.replace(second=0, microsecond=0)))


def _excel_safe(value):
    """openpyxl 仅支持基础标量：UUID（FK pk）转字符串、带时区 datetime/time 转本地
    naive（Excel 不接受 tzinfo）、dict/list 转 JSON 文本，其余原样。"""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime) and timezone.is_aware(value):
        return timezone.localtime(value).replace(tzinfo=None)
    if isinstance(value, dt_time) and getattr(value, "tzinfo", None) is not None:
        return value.replace(tzinfo=None)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return value


def _render_workbook(report, user) -> tuple:
    """执行数据集并渲染 xlsx 到内存。返回 (bytes, sheet_rows)。"""
    from openpyxl import Workbook

    from system.utils.dataset import aggregate_dataset, execute_dataset

    wb = Workbook()
    ws = wb.active
    ws.title = str(_("Report"))

    if report.mode == "aggregate":
        result = aggregate_dataset(
            report.dataset,
            user,
            group_by=report.group_by,
            metric=report.metric or "count",
            date_trunc=report.date_trunc or None,
            value_field=report.value_field or None,
        )
        ws.append([_("Name"), _("Value")])
        rows = [[_excel_safe(item["name"]), _excel_safe(item["value"])] for item in result["series"]]
    else:
        result = execute_dataset(report.dataset, user)
        ws.append(list(result["columns"]))
        rows = [[_excel_safe(row.get(col)) for col in result["columns"]] for row in result["rows"]]
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue(), len(rows)


def _deliver_email(report, filename: str, content: bytes, rows: int) -> None:
    subject = "{} - {}".format(report.name, timezone.localtime().strftime("%Y-%m-%d %H:%M"))
    body = str(_("Scheduled report {}. {} rows generated. The xlsx file is attached.").format(subject, rows))
    mail = EmailMessage(subject=subject, body=body, to=list(report.recipients or []))
    mail.attach(filename, content, EXPORT_MIME)
    mail.send()


def _precreate_record(report) -> str:
    """预创建 ExportRecord（下载中心条目），pk 即派发的 celery task_id。"""
    from system.models.export import ExportRecord

    record = ExportRecord.objects.create(
        name=f"{report.name}-{timezone.localtime():%Y%m%d%H%M%S}",
        module="Report",
        file_format="xlsx",
        creator=report.creator,
    )
    return str(record.pk)


@shared_task
@register_as_period_task(crontab="5 * * * *", description="定时报表调度分发")
def dispatch_scheduled_reports():
    """每小时扫描 active 报表并派发到期的执行任务（三档频次；cron 报表由每分钟任务负责）。"""
    from system.models.dataset import Report

    dispatched = 0
    for report in Report.objects.filter(is_active=True, cron_expression="").iterator():
        try:
            if not report_due(report):
                continue
        except Exception:  # noqa: BLE001 单条判定异常不中断扫描
            logger.warning("report schedule check failed: %s", report.pk, exc_info=True)
            continue
        run_scheduled_report.apply_async(kwargs={"report_id": str(report.pk)}, task_id=_precreate_record(report))
        dispatched += 1
    if dispatched:
        logger.info("dispatched %s scheduled report(s)", dispatched)
    return dispatched


@shared_task
@register_as_period_task(crontab="* * * * *", description="定时报表 cron 表达式调度分发")
def dispatch_cron_reports():
    """每分钟扫描带 cron 表达式的 active 报表并派发（三档报表由每小时任务负责，职责互斥）。"""
    from system.models.dataset import Report

    dispatched = 0
    for report in Report.objects.filter(is_active=True).exclude(cron_expression="").iterator():
        try:
            if not report_due(report):
                continue
        except Exception:  # noqa: BLE001 单条判定异常不中断扫描
            logger.warning("report cron check failed: %s", report.pk, exc_info=True)
            continue
        run_scheduled_report.apply_async(kwargs={"report_id": str(report.pk)}, task_id=_precreate_record(report))
        dispatched += 1
    if dispatched:
        logger.info("dispatched %s cron scheduled report(s)", dispatched)
    return dispatched


@shared_task(bind=True)
def run_scheduled_report(self, report_id: str):
    """执行单个报表：数据集渲染 xlsx → ExportRecord（下载中心）→ 邮件附件。

    task_id == 预创建 ExportRecord.pk（CeleryTaskRecordModel 契约）。
    """
    from system.models.dataset import Report
    from system.models.export import ExportRecord
    from system.models.upload import UploadFile

    record = ExportRecord.objects.filter(pk=self.request.id).first()
    report = Report.objects.filter(pk=report_id).select_related("dataset", "creator").first()
    if record is None or report is None:
        logger.warning("scheduled report record/report missing: %s", report_id)
        return 0

    record.status = ExportRecord.Status.RUNNING
    record.save(update_fields=["status", "updated_time"])
    user = report.creator
    try:
        content, rows = _render_workbook(report, user)
        filename = f"{report.name}-{timezone.localtime():%Y%m%d%H%M}.xlsx"
        upload = UploadFile(
            filename=filename,
            filesize=len(content),
            mime_type=EXPORT_MIME,
            is_tmp=True,
            is_upload=False,
            creator=user,
        )
        upload.filepath.save(filename, ContentFile(content), save=False)
        upload.save()
        record.file = upload
        record.rows = rows
        record.status = ExportRecord.Status.SUCCESS
        record.progress = 100
        record.save(update_fields=["file", "rows", "status", "progress", "updated_time"])

        error = ""
        try:
            _deliver_email(report, filename, content, rows)
        except Exception as exc:  # noqa: BLE001 邮件失败不回滚产物
            error = f"email failed: {exc}"
            logger.warning("scheduled report email failed: %s", report.pk, exc_info=True)
        report.last_run_at = timezone.now()
        report.last_status = "SUCCESS" if not error else "SUCCESS_WITH_EMAIL_ERROR"
        report.save(update_fields=["last_run_at", "last_status", "updated_time"])
        record.error = error[:2000] if error else None
        record.save(update_fields=["error", "updated_time"])
        logger.info("scheduled report done: %s rows=%s email=%s", report.pk, rows, not error)
        return rows
    except Exception as exc:
        record.status = ExportRecord.Status.FAILURE
        record.error = str(exc)[:2000]
        record.save(update_fields=["status", "error", "updated_time"])
        report.last_run_at = timezone.now()
        report.last_status = "FAILURE"
        report.save(update_fields=["last_run_at", "last_status", "updated_time"])
        logger.warning("scheduled report failed: %s", report.pk, exc_info=True)
        raise


@shared_task
def schedule_report_run(report_id: str):
    """立即运行入口（管理页 run 动作）：预创建 ExportRecord 并按契约派发。"""
    from system.models.dataset import Report

    report = Report.objects.filter(pk=report_id).first()
    if report is None:
        return ""
    task_id = _precreate_record(report)
    run_scheduled_report.apply_async(kwargs={"report_id": str(report.pk)}, task_id=task_id)
    return task_id
