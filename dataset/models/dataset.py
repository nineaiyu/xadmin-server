#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据集与仪表盘（可视化一期）。

数据集 = 模型白名单内的受控查询定义（禁原生 SQL）：模型/字段/op 三层白名单
在保存与执行双侧校验，行级过滤复用数据权限编译器入口 `get_filter_queryset`
（fail-closed）。仪表盘 = 布局 JSON + 卡片引用数据集，浏览时按浏览者权限执行。
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel


class Dataset(DbAuditModel, DbUuidModel):
    """数据集：绑定白名单模型的列/过滤/排序定义。"""

    class Visibility(models.TextChoices):
        PERSONAL = "personal", _("Personal")
        SHARED = "shared", _("Shared")

    name = models.CharField(_("Name"), max_length=128, unique=True)
    description = models.CharField(_("Description"), max_length=512, blank=True, default="")
    bound_model = models.CharField(
        _("Bound model"),
        max_length=128,
        help_text=_("label_lower of a whitelisted model, e.g. system.userinfo"),
    )
    columns = models.JSONField(_("Columns"), default=list, help_text=_("Whitelisted field names"))
    filters = models.JSONField(_("Filters"), default=list, help_text=_("[{field, op, value}] with whitelisted ops"))
    ordering = models.CharField(_("Ordering"), max_length=128, blank=True, default="")
    row_limit = models.IntegerField(_("Row limit"), default=1000, help_text=_("Hard cap 5000"))
    config = models.JSONField(_("Config"), default=dict, blank=True, help_text=_('{"date_field": ...} for trend'))
    visibility = models.CharField(
        _("Visibility"), max_length=16, choices=Visibility.choices, default=Visibility.PERSONAL, db_index=True
    )

    class Meta:
        db_table = "system_dataset"  # 3.1 拆分批次4：迁 dataset app，表名不变
        verbose_name = _("Dataset")
        verbose_name_plural = _("Datasets")
        ordering = ("-created_time",)

    def __str__(self):
        return f"{self.name}({self.bound_model})"


class Dashboard(DbAuditModel, DbUuidModel):
    """仪表盘：布局 JSON + 卡片引用数据集（卡片内嵌，无子表）。"""

    class Visibility(models.TextChoices):
        PERSONAL = "personal", _("Personal")
        SHARED = "shared", _("Shared")

    name = models.CharField(_("Name"), max_length=128)
    layout = models.JSONField(_("Layout"), default=list, help_text=_("[{id, dataset, title, chart_type, ...}]"))
    visibility = models.CharField(
        _("Visibility"), max_length=16, choices=Visibility.choices, default=Visibility.PERSONAL, db_index=True
    )

    class Meta:
        db_table = "system_dashboard"  # 3.1 拆分批次4：迁 dataset app，表名不变
        verbose_name = _("Dashboard")
        verbose_name_plural = _("Dashboards")
        ordering = ("-created_time",)

    def __str__(self):
        return f"{self.name}({self.visibility})"


class Screen(DbAuditModel, DbUuidModel):
    """大屏模板：跨仪表盘的全屏轮播配置。"""

    name = models.CharField(_("Name"), max_length=128, unique=True)
    dashboards = models.JSONField(_("Dashboards"), default=list, help_text=_("Ordered dashboard pks"))
    interval = models.IntegerField(_("Interval"), default=15, help_text=_("Seconds per dashboard"))
    refresh = models.IntegerField(_("Refresh"), default=60, help_text=_("Data refresh seconds"))
    visibility = models.CharField(
        _("Visibility"),
        max_length=16,
        choices=Dataset.Visibility.choices,
        default=Dataset.Visibility.PERSONAL,
        db_index=True,
    )

    class Meta:
        db_table = "system_screen"  # 3.1 拆分批次4：迁 dataset app，表名不变
        verbose_name = _("Screen")
        verbose_name_plural = _("Screens")
        ordering = ("-created_time",)

    def __str__(self):
        return f"{self.name}({len(self.dashboards)})"


class Report(DbAuditModel, DbUuidModel):
    """定时报表：数据集 + 调度 + 投递渠道与收件人（邮件 + IM）。"""

    class Frequency(models.TextChoices):
        DAILY = "daily", _("Daily")
        WEEKLY = "weekly", _("Weekly")
        MONTHLY = "monthly", _("Monthly")

    name = models.CharField(_("Name"), max_length=128, unique=True)
    dataset = models.ForeignKey(Dataset, on_delete=models.PROTECT, verbose_name=_("Dataset"))
    mode = models.CharField(
        _("Mode"), max_length=16, default="rows", choices=[("rows", _("Rows")), ("aggregate", _("Aggregate"))]
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

    class Meta:
        db_table = "system_report"  # 3.1 拆分批次4：迁 dataset app，表名不变
        verbose_name = _("Report")
        verbose_name_plural = _("Reports")
        ordering = ("-created_time",)

    def __str__(self):
        return f"{self.name}({self.frequency})"
