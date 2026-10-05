#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""定时报表渲染与投递（自 analysis_tasks 拆分，行为不变）。

- 渲染：执行数据集 → xlsx 到内存（design 决定明细列/行数上限，聚合组件各落独立
  sheet）；单组件失败（字段被删、字段权限收紧等）只写一行提示，不拖垮整份报表；
- 投递：邮件携带附件；IM 为文本消息（报表名/行数/下载中心提示），逐渠道独立失败
  并返回明细，任一渠道失败仅记 error 与交付状态，不回滚产物。

产物 MIME 与落盘复用 task 域统一导出服务（task.services），本模块只保留报表
工作簿渲染器与多渠道投递（下载中心链路的报表侧薄适配）。
"""

import io
import json
from datetime import datetime
from datetime import time as dt_time
from uuid import UUID

from django.core.mail import EmailMessage
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)


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

    批次二：`design` 决定明细列与行数上限（空 = 存量全列口径），并为每个聚合组件
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
    from task.services import mime_type_for

    subject = "{} - {}".format(report.name, timezone.localtime().strftime("%Y-%m-%d %H:%M"))
    body = str(_("Scheduled report {}. {} rows generated. The xlsx file is attached.").format(subject, rows))
    # 设计报表：正文补一行组件清单（图表数据在附件的独立 sheet 内，正文不适配 HTML）
    charts = [str(component.get("title") or component.get("type")) for component in design_components(report.design)]
    if charts:
        body = "{}\n{}".format(body, str(_("Designed charts: {}").format(", ".join(charts))))
    mail = EmailMessage(subject=subject, body=body, to=list(report.recipients or []))
    mail.attach(filename, content, mime_type_for("xlsx"))
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
    from identity.models import UserInfo
    from notifications.backends import BACKEND

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
