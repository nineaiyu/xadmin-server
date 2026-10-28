#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""大屏模型：大屏定义、共享模板、模板版本与云端剪贴板。"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel

from .visibility import Visibility


class Screen(DbAuditModel, DbUuidModel):
    """大屏模板：跨仪表盘的全屏轮播 + 两种画布形态（栅格 / 自由像素）。

    两种画布载荷各自保留（模式切换是坐标语义的有损变更，双字段保证来回切换不丢布局）；
    投屏形态判定与窗格校验见 ``dataset/utils/screen_layout.py``。
    """

    class LayoutMode(models.TextChoices):
        GRID = "grid", _("Grid")
        FREE = "free", _("Free canvas")

    class ScreenTheme(models.TextChoices):
        DARK = "dark", _("Dark")
        LIGHT = "light", _("Light")
        AUTO = "auto", _("Follow site")

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
        choices=Visibility.choices,
        default=Visibility.PERSONAL,
        db_index=True,
    )
    #: 投屏主题：dark 恒深色（缺省，存量观感）/ light 浅色画布 / auto 跟随站点明暗
    theme = models.CharField(
        _("Theme"),
        max_length=8,
        choices=ScreenTheme.choices,
        default=ScreenTheme.DARK,
        help_text=_("Display theme: dark / light / follow site"),
    )
    #: 大屏主题色（hex，如 #006be6）：空 = 跟随站点主色
    theme_color = models.CharField(
        _("Theme color"),
        max_length=9,
        blank=True,
        default="",
        help_text=_("Screen accent color (hex); empty = follow site primary"),
    )
    #: 布局版本：每次布局载荷实质变更 +1；回滚同样生成新版本（历史不删除）
    layout_version = models.PositiveIntegerField(_("Layout version"), default=1)

    class Meta:
        verbose_name = _("Screen")
        verbose_name_plural = _("Screens")
        ordering = ("-created_time",)

    def __str__(self) -> str:
        return f"{self.name}({len(self.dashboards)})"


class ScreenVersion(DbAuditModel, DbUuidModel):
    """大屏本体的历史版本：保存布局时自动记一版，可回滚。

    存两份画布载荷与模式（模式切换是坐标语义的有损变更，回滚必须同时还原两份），
    名称 / 可见性 / 主题 / 轮播清单跟随主表。保留最近 ``SCREEN_VERSION_KEEP`` 版。
    """

    screen = models.ForeignKey(
        Screen,
        on_delete=models.CASCADE,
        related_name="versions",
        verbose_name=_("Screen"),
    )
    version = models.PositiveIntegerField(_("Version"))
    layout = models.JSONField(_("Layout"), default=list, blank=True)
    layout_mode = models.CharField(_("Layout mode"), max_length=8, choices=Screen.LayoutMode.choices)
    free_layout = models.JSONField(_("Free layout"), default=dict, blank=True)

    class Meta:
        verbose_name = _("Screen version")
        verbose_name_plural = _("Screen versions")
        ordering = ("-version",)
        constraints = [models.UniqueConstraint(fields=("screen", "version"), name="uniq_screen_version")]

    def __str__(self) -> str:
        return f"{self.screen_id}#v{self.version}"


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
        choices=Visibility.choices,
        default=Visibility.PERSONAL,
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
