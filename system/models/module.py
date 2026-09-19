#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""功能模块裁剪的后台覆盖配置。"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel

# 单行覆盖的固定标识（唯一约束保证库里最多一行）
OVERRIDE_KEY = "module"


class ModuleOverride(DbAuditModel, DbUuidModel):
    """模块裁剪的后台覆盖（单行；管理页写入，重启后生效）。

    存在本行时，模块解析以本行为准：``preset`` / ``enable`` / ``disable`` 整体替换
    部署基线（config.yml / 环境变量），无行时完全按部署基线解析。裁剪语义、优先级
    与「待重启生效」说明见 docs/architecture/模块化与功能裁剪.md。
    """

    key = models.CharField(
        max_length=32, unique=True, default=OVERRIDE_KEY, editable=False, verbose_name=_("Config name")
    )
    preset = models.CharField(max_length=16, verbose_name=_("Module preset"))
    enable = models.JSONField(default=list, blank=True, verbose_name=_("Enabled modules"))
    disable = models.JSONField(default=list, blank=True, verbose_name=_("Disabled modules"))

    class Meta:
        verbose_name = _("Module override")
        verbose_name_plural = verbose_name
        ordering = ["created_time"]

    def __str__(self):
        return f"{self.preset}(+{len(self.enable)}/-{len(self.disable)})"
