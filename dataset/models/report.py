#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""定时报表模型：数据集 + 调度 + 投递渠道与收件人。"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel

from .dataset import Dataset


class Report(DbAuditModel, DbUuidModel):
    """定时报表：数据集 + 调度 + 投递渠道与收件人（邮件 + IM）。

    可见性口径（产品决策）：报表定义对全员共享只读，不设 personal/shared 维度；
    真正的数据边界由数据集行级权限 + 字段权限承担。收件人列表仍按字段掩码输出。
    """

    class Frequency(models.TextChoices):
        DAILY = "daily", _("Daily")
        WEEKLY = "weekly", _("Weekly")
        MONTHLY = "monthly", _("Monthly")

    name = models.CharField(_("Name"), max_length=128, unique=True)
    dataset = models.ForeignKey(Dataset, on_delete=models.PROTECT, verbose_name=_("Dataset"))
    mode = models.CharField(
        _("Mode"), max_length=16, default="rows", choices=[("rows", _("Rows")), ("aggregate", _("Aggregate"))]
    )
    #: 报表设计：明细列 + 行数上限 + 聚合组件；空 = 存量口径（全列明细单表）
    design = models.JSONField(
        _("Design"),
        default=dict,
        blank=True,
        help_text=_("Report designer payload (empty = legacy single-sheet export)"),
    )
    group_by = models.CharField(_("Group by"), max_length=128, blank=True, default="")
    metric = models.CharField(_("Metric"), max_length=16, default="count", blank=True)
    date_trunc = models.CharField(_("Date trunc"), max_length=16, blank=True, default="")
    value_field = models.CharField(_("Value field"), max_length=128, blank=True, default="")
    frequency = models.CharField(_("Frequency"), max_length=16, choices=Frequency.choices, default=Frequency.DAILY)
    send_time = models.CharField(_("Send time"), max_length=5, default="08:00", help_text=_("HH:MM"))
    # cron 表达式（五段，分钟级）：非空时优先于 frequency/send_time 三档
    cron_expression = models.CharField(
        _("Cron expression"), max_length=64, blank=True, default="", help_text=_("5-field cron, takes precedence")
    )
    weekday = models.IntegerField(_("Weekday"), default=0, help_text=_("0=Monday, weekly only"))
    # 每月几号投递（1~28，避开月末歧义）：monthly only
    month_day = models.IntegerField(_("Month day"), default=1, help_text=_("1-28, monthly only"))
    recipients = models.JSONField(_("Recipients"), default=list, help_text=_("Email addresses"))
    # 投递渠道：email / dingtalk / wecom / feishu；空 = 仅邮件（存量兼容）
    notify_channels = models.JSONField(_("Notify channels"), default=list, blank=True, help_text=_("Delivery channels"))
    # IM 收件人（用户主键）：投递时按各渠道 OAuth 绑定的可达性过滤
    im_recipients = models.JSONField(
        _("IM recipients"), default=list, blank=True, help_text=_("User pks receiving IM messages")
    )
    is_active = models.BooleanField(_("Is active"), default=True)
    last_run_at = models.DateTimeField(_("Last run at"), null=True, blank=True)
    last_status = models.CharField(_("Last status"), max_length=32, blank=True, default="")
    #: 设计版本：每次 design 实质变更 +1；回滚同样生成新版本（历史不删除）
    design_version = models.PositiveIntegerField(_("Design version"), default=1)

    class Meta:
        verbose_name = _("Report")
        verbose_name_plural = _("Reports")
        ordering = ("-created_time",)

    def __str__(self) -> str:
        return f"{self.name}({self.frequency})"


class ReportVersion(DbAuditModel, DbUuidModel):
    """报表设计的历史版本：保存设计时自动记一版，可回滚。

    只存会被设计器编辑的 ``design`` 载荷；名称 / 调度 / 收件人跟随主表。
    每个报表保留最近 ``REPORT_VERSION_KEEP`` 版（超出裁剪最旧）。
    """

    report = models.ForeignKey(
        Report,
        on_delete=models.CASCADE,
        related_name="versions",
        verbose_name=_("Report"),
    )
    version = models.PositiveIntegerField(_("Version"))
    design = models.JSONField(_("Design"), default=dict, blank=True)

    class Meta:
        verbose_name = _("Report version")
        verbose_name_plural = _("Report versions")
        ordering = ("-version",)
        constraints = [models.UniqueConstraint(fields=("report", "version"), name="uniq_report_version")]

    def __str__(self) -> str:
        return f"{self.report_id}#v{self.version}"
