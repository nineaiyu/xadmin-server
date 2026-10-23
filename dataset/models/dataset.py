#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据集与仪表盘（可视化一期）。

数据集 = 模型白名单内的受控查询定义（禁原生 SQL）：模型/字段/op 三层白名单
在保存与执行双侧校验，行级过滤复用数据权限编译器入口 `get_filter_queryset`
（fail-closed）。仪表盘 = 布局 JSON + 卡片引用数据集，浏览时按浏览者权限执行。

模型已按域拆到同 app 的 ``dashboard`` / ``screen`` / ``report`` 模块；本模块保留
``Dataset`` 定义并再导出其余模型，存量 ``dataset.models.dataset`` import 不变。
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel

from .visibility import Visibility


class Dataset(DbAuditModel, DbUuidModel):
    """数据集：绑定白名单模型的列/过滤/排序定义。"""

    # 兼容历史访问面 `Dataset.Visibility`（枚举单源在 visibility.py）
    Visibility = Visibility

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


# 同 app 其余模型的兼容再导出（历史 import 面零改动；定义在各自域文件）
from .dashboard import Dashboard  # noqa: E402,F401
from .report import Report  # noqa: E402,F401
from .screen import (  # noqa: E402,F401
    Screen,
    ScreenClipboard,
    ScreenTemplate,
    ScreenTemplateVersion,
)

__all__ = [
    "Dataset",
    "Dashboard",
    "Report",
    "Screen",
    "ScreenClipboard",
    "ScreenTemplate",
    "ScreenTemplateVersion",
]
