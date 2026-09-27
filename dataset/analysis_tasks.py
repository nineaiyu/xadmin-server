#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""定时报表任务：调度分发 + 执行渲染 + 多渠道路送达（邮件 + IM）。

- 分发器每小时跑一次（crontab "5 * * * *"），命中 frequency/send_time/weekday
  的 active 报表派发执行；执行与分发解耦（长渲染不阻塞扫描）；
- 执行以**创建者**权限上下文运行数据集（menu 上下文为空 ⇒ 仅未绑菜单的全局
  授权生效，fail-closed 语义不变）；
- 产物复用下载中心：派发方预创建 ExportRecord（pk == celery task_id 契约），
  xlsx 渲染后落 UploadFile 并挂 record；
- 投递按 `notify_channels` 逐渠道独立执行（空 = 仅邮件）：邮件携带附件，IM 为
  文本消息（报表名/行数/下载中心提示，收件人取 `im_recipients` 用户主键并按各
  渠道 OAuth 绑定可达性过滤）；任一渠道失败仅记 error 与交付状态，不回滚产物。
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


def _sheet_title(component, index: int) -> str:
    """组件 sheet 名：标题（非法字符替换、截断）+ 序号前缀（保证唯一且 ≤31 字符）。"""
    title = str(component.get("title") or component.get("type") or _("Chart"))
    for char in "[]:*?/\\":
        title = title.replace(char, " ")
    title = title.strip()[:24] or str(_("Chart"))
    return f"{index}-{title}"


def _render_component_sheet(wb, report, user, component, index: int) -> None:
    """单个聚合组件落一张独立 sheet（名称/值两列）。

    单组件失败（字段被删、字段权限收紧等）只写一行提示，不拖垮整份报表投递：
    投递的可用性优先于单个图表，失败原因落日志供排查。
    """
    from dataset.utils.dataset import aggregate_dataset

    sheet = wb.create_sheet(_sheet_title(component, index))
    sheet.append([str(_("Name")), str(_("Value"))])
    try:
        result = aggregate_dataset(
            report.dataset,
            user,
            group_by=component.get("group_by", ""),
            metric=component.get("metric") or "count",
            date_trunc=component.get("date_trunc") or None,
            value_field=component.get("value_field") or None,
        )
    except Exception:  # noqa: BLE001 单组件失败不影响其余 sheet
        logger.warning("scheduled report component failed: %s", component.get("id"), exc_info=True)
        sheet.append([str(_("Chart data unavailable")), ""])
        return
    for item in result["series"]:
        sheet.append([_excel_safe(item["name"]), _excel_safe(item["value"])])


def _render_workbook(report, user) -> tuple:
    """执行数据集并渲染 xlsx 到内存。返回 (bytes, 明细行数)。

    P2.2 批次二：`design` 决定明细列与行数上限（空 = 存量全列口径），并为每个聚合组件
    追加独立 sheet；`mode == "aggregate"` 的存量单表行为不变。
    """
    from openpyxl import Workbook

    from dataset.utils.dataset import aggregate_dataset, execute_dataset
    from dataset.utils.report_design import design_components, design_export_columns, design_table_limit

    design = report.design or {}
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
        # openpyxl 只接受 str：gettext_lazy 代理不是 str 实例，必须显式转换
        ws.append([str(_("Name")), str(_("Value"))])
        rows = [[_excel_safe(item["name"]), _excel_safe(item["value"])] for item in result["series"]]
    else:
        result = execute_dataset(report.dataset, user)
        columns = design_export_columns(design, result["columns"])
        limit = design_table_limit(design)
        ws.append(list(columns))
        rows = [[_excel_safe(row.get(col)) for col in columns] for row in result["rows"][:limit]]
    for row in rows:
        ws.append(row)

    for index, component in enumerate(design_components(design), start=1):
        _render_component_sheet(wb, report, user, component, index)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue(), len(rows)


def _deliver_email(report, filename: str, content: bytes, rows: int) -> None:
    from dataset.utils.report_design import design_components

    subject = "{} - {}".format(report.name, timezone.localtime().strftime("%Y-%m-%d %H:%M"))
    body = str(_("Scheduled report {}. {} rows generated. The xlsx file is attached.").format(subject, rows))
    # 设计报表：正文补一行组件清单（图表数据在附件的独立 sheet 内，正文不适配 HTML）
    charts = [str(component.get("title") or component.get("type")) for component in design_components(report.design)]
    if charts:
        body = "{}\n{}".format(body, str(_("Designed charts: {}").format(", ".join(charts))))
    mail = EmailMessage(subject=subject, body=body, to=list(report.recipients or []))
    mail.attach(filename, content, EXPORT_MIME)
    mail.send()


def report_notify_channels(report) -> list:
    """报表投递渠道清单：非法取值忽略；空 = 仅邮件（存量数据与旧客户端兼容）。"""
    from dataset.serializers.analysis import REPORT_NOTIFY_CHANNELS

    channels = [item for item in (report.notify_channels or []) if item in REPORT_NOTIFY_CHANNELS]
    return channels or ["email"]


def _deliver_im(report, rows: int) -> list:
    """IM 渠道投递（文本消息：报表名/行数/下载中心提示）。

    逐渠道独立失败并返回失败明细（``渠道: 原因``）；未配置的渠道记入明细而非静默跳过。
    消息体不携带附件（各 IM 后端 send_msg 为文本协议），产物统一在下载中心取用。
    """
    from notifications.backends import BACKEND
    from system.models import UserInfo

    channels = [item for item in report_notify_channels(report) if item != "email"]
    if not channels:
        return []
    recipients = list(UserInfo.objects.filter(pk__in=report.im_recipients or [], is_active=True))
    if not recipients:
        return ["{}: no active recipients".format("/".join(channels))]
    subject = "{} - {}".format(report.name, timezone.localtime().strftime("%Y-%m-%d %H:%M"))
    message = str(
        _("Scheduled report generated, {} rows. Open the download center to view or download it.").format(rows)
    )
    errors = []
    for channel in channels:
        try:
            backend = BACKEND(channel)
            if not backend.is_enable:
                errors.append(f"{channel}: not configured")
                continue
            backend.client.send_msg(recipients, message, subject=subject)
        except Exception as exc:  # noqa: BLE001 单渠道失败不影响其它渠道
            errors.append(f"{channel}: {exc}")
            logger.warning("scheduled report im delivery failed: %s", channel, exc_info=True)
    return errors


def _precreate_record(report) -> str:
    """预创建 ExportRecord（下载中心条目），pk 即派发的 celery task_id。

    params 记 ``report_id``：任务中心「重跑」按记录即可重放同一报表。
    """
    from system.models.export import ExportRecord

    record = ExportRecord.objects.create(
        name=f"{report.name}-{timezone.localtime():%Y%m%d%H%M%S}",
        module="Report",
        file_format="xlsx",
        params={"report_id": str(report.pk)},
        creator=report.creator,
    )
    return str(record.pk)


@shared_task
@register_as_period_task(crontab="5 * * * *", description="定时报表调度分发", module="analysis")
def dispatch_scheduled_reports():
    """每小时扫描 active 报表并派发到期的执行任务（三档频次；cron 报表由每分钟任务负责）。"""
    from dataset.models.dataset import Report

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
@register_as_period_task(crontab="* * * * *", description="定时报表 cron 表达式调度分发", module="analysis")
def dispatch_cron_reports():
    """每分钟扫描带 cron 表达式的 active 报表并派发（三档报表由每小时任务负责，职责互斥）。"""
    from dataset.models.dataset import Report

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
    from dataset.models.dataset import Report
    from system.models.export import ExportRecord
    from system.models.upload import UploadFile
    from system.utils.task_progress import KIND_REPORT, update_progress

    record = ExportRecord.objects.filter(pk=self.request.id).first()
    report = Report.objects.filter(pk=report_id).select_related("dataset", "creator").first()
    if record is None or report is None:
        logger.warning("scheduled report record/report missing: %s", report_id)
        return 0

    record.status = ExportRecord.Status.RUNNING
    record.save(update_fields=["status", "updated_time"])
    user = report.creator
    # 统一进度：报表此前只有终态 100，此处补中间里程碑（查询 → 渲染 → 落盘）
    update_progress(KIND_REPORT, record.pk, 20, stage=_("Querying dataset"))
    try:
        content, rows = _render_workbook(report, user)
        update_progress(KIND_REPORT, record.pk, 80, stage=_("Rendering workbook"))
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

        errors = []
        channels = report_notify_channels(report)
        if "email" in channels:
            try:
                _deliver_email(report, filename, content, rows)
            except Exception as exc:  # noqa: BLE001 邮件失败不回滚产物
                errors.append(f"email: {exc}")
                logger.warning("scheduled report email failed: %s", report.pk, exc_info=True)
        errors.extend(_deliver_im(report, rows))
        report.last_run_at = timezone.now()
        report.last_status = "SUCCESS" if not errors else "SUCCESS_WITH_DELIVERY_ERROR"
        report.save(update_fields=["last_run_at", "last_status", "updated_time"])
        record.error = "; ".join(errors)[:2000] if errors else None
        record.save(update_fields=["error", "updated_time"])
        logger.info("scheduled report done: %s rows=%s channels=%s", report.pk, rows, channels)
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
    from dataset.models.dataset import Report

    report = Report.objects.filter(pk=report_id).first()
    if report is None:
        return ""
    task_id = _precreate_record(report)
    run_scheduled_report.apply_async(kwargs={"report_id": str(report.pk)}, task_id=task_id)
    return task_id
