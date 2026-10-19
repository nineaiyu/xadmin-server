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
        verbose_name = _("Dataset")
        verbose_name_plural = _("Datasets")
        ordering = ("-created_time",)

    def __str__(self) -> str:
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
        verbose_name = _("Dashboard")
        verbose_name_plural = _("Dashboards")
        ordering = ("-created_time",)

    def __str__(self) -> str:
        return f"{self.name}({self.visibility})"


class Screen(DbAuditModel, DbUuidModel):
    """大屏模板：跨仪表盘的全屏轮播 + 两种画布形态（栅格 / 自由像素）。

    两种画布载荷各自保留（模式切换是坐标语义的有损变更，双字段保证来回切换不丢布局）；
    投屏形态判定与窗格校验见 ``dataset/utils/screen_layout.py``。
    """

    class LayoutMode(models.TextChoices):
        GRID = "grid", _("Grid")
        FREE = "free", _("Free canvas")

    name = models.CharField(_("Name"), max_length=128, unique=True)
    dashboards = models.JSONField(_("Dashboards"), default=list, help_text=_("Ordered dashboard pks"))
    #: 画布窗格（12 列栅格绝对定位）：空 = 维持仪表盘轮播（存量数据与旧客户端零影响）
    layout = models.JSONField(
        _("Layout"),
        default=list,
        blank=True,
        help_text=_("Canvas panes on a 12-column grid (empty = carousel mode)"),
    )
    #: 画布模式：grid 用 layout，free 用 free_layout（仅决定画布态用哪份载荷）
    layout_mode = models.CharField(
        _("Layout mode"),
        max_length=8,
        choices=LayoutMode.choices,
        default=LayoutMode.GRID,
        help_text=_("Canvas mode: grid uses layout, free uses free_layout"),
    )
    #: 自由画布载荷：{"design_w", "design_h", "panes"}，窗格为像素坐标（允许重叠，数组序即层级）
    free_layout = models.JSONField(
        _("Free layout"),
        default=dict,
        blank=True,
        help_text=_("Free pixel canvas: {design_w, design_h, panes} (empty = none)"),
    )
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
        verbose_name = _("Screen")
        verbose_name_plural = _("Screens")
        ordering = ("-created_time",)

    def __str__(self) -> str:
        return f"{self.name}({len(self.dashboards)})"


class ScreenTemplate(DbAuditModel, DbUuidModel):
    """大屏模板（团队共享）：自由画布布局的可复用切片。

    载荷与 ``Screen.free_layout`` **完全同形**（``{design_w, design_h, panes}``），
    归一化 / 校验复用 ``dataset.utils.screen_layout``（同一份白名单与常量），因此
    「保存模板」与「保存大屏」不会出现两套坐标口径。可见性同 Dataset（personal 仅
    创建者 / shared 全员），非创建者改删由行级归属守卫拒绝（1003，同 Screen）。
    """

    name = models.CharField(_("Name"), max_length=128, unique=True)
    free_layout = models.JSONField(
        _("Free layout"),
        default=dict,
        blank=True,
        help_text=_("Template canvas: {design_w, design_h, panes} (empty = none)"),
    )
    group = models.CharField(
        _("Group"),
        max_length=40,
        blank=True,
        default="",
        db_index=True,
        help_text=_("Template folder/group name (empty = ungrouped)"),
    )
    visibility = models.CharField(
        _("Visibility"),
        max_length=16,
        choices=Dataset.Visibility.choices,
        default=Dataset.Visibility.PERSONAL,
        db_index=True,
    )

    class Meta:
        verbose_name = _("Screen template")
        verbose_name_plural = _("Screen templates")
        ordering = ("-created_time",)

    def __str__(self) -> str:
        return f"{self.name}({len(self.free_layout.get('panes') or [])})"


class ScreenTemplateVersion(DbAuditModel, DbUuidModel):
    """大屏模板的历史版本：保存模板内容时自动记一版，可回滚。

    只存「会被编辑的三件事」——名称 / 分组 / 载荷；可见性与归属跟随主表。
    每个模板保留最近 ``SCREEN_TEMPLATE_VERSION_KEEP`` 版（超出裁剪最旧）。
    """

    template = models.ForeignKey(
        ScreenTemplate,
        on_delete=models.CASCADE,
        related_name="versions",
        verbose_name=_("Template"),
    )
    version = models.PositiveIntegerField(_("Version"))
    name = models.CharField(_("Name"), max_length=128)
    group = models.CharField(_("Group"), max_length=40, blank=True, default="")
    free_layout = models.JSONField(_("Free layout"), default=dict, blank=True)

    class Meta:
        verbose_name = _("Screen template version")
        verbose_name_plural = _("Screen template versions")
        ordering = ("-version",)
        constraints = [models.UniqueConstraint(fields=("template", "version"), name="uniq_screen_template_version")]

    def __str__(self) -> str:
        return f"{self.name}#v{self.version}"


class ScreenClipboard(DbAuditModel, DbUuidModel):
    """云端剪贴板：按用户单槽，让「复制 → 换设备/浏览器 → 粘贴」成立。

    载荷与自由画布同形（``{design_w, design_h, panes}``），归一化复用
    ``dataset.utils.screen_layout``；一个用户只有一条记录（``user`` 唯一）。
    """

    user = models.OneToOneField(
        "identity.UserInfo",
        on_delete=models.CASCADE,
        related_name="screen_clipboard",
        verbose_name=_("User"),
    )
    free_layout = models.JSONField(
        _("Free layout"),
        default=dict,
        blank=True,
        help_text=_("Clipboard canvas: {design_w, design_h, panes}"),
    )

    class Meta:
        verbose_name = _("Screen clipboard")
        verbose_name_plural = _("Screen clipboards")
        ordering = ("-updated_time",)

    def __str__(self) -> str:
        return f"clipboard:{self.user_id}"


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

    class Meta:
        verbose_name = _("Report")
        verbose_name_plural = _("Reports")
        ordering = ("-created_time",)

    def __str__(self) -> str:
        return f"{self.name}({self.frequency})"
