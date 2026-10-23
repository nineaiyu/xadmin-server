#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""仪表盘模型：布局 JSON + 卡片内嵌引用数据集。"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel


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
