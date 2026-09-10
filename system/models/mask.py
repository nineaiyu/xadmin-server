#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""字段级数据脱敏规则。

- 规则按 (model, field) 定位：model 为模型 label_lower（如 system.userinfo），field 为序列化字段名；
- 应用点统一在 BaseModelSerializer.to_representation（输出侧统一掩码，无前端侵入）；
- roles 为空 = 对全部非超管生效；非空 = 仅命中角色内用户生效；
- 规则变更（含 roles m2m 直改）由 system/signal_handler.py 失效缓存。
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel


class DataMaskRule(DbUuidModel, DbAuditModel):
    """字段级脱敏规则表"""

    class MaskType(models.TextChoices):
        PHONE = "phone", _("Phone")
        IDCARD = "idcard", _("ID card")
        EMAIL = "email", _("Email")
        NAME = "name", _("Name")
        BANKCARD = "bankcard", _("Bank card")
        CUSTOM = "custom", _("Custom")

    model = models.CharField(_("Model"), max_length=128, help_text=_("Model label like system.userinfo"))
    field = models.CharField(_("Field"), max_length=64)
    mask_type = models.CharField(_("Mask type"), max_length=16, choices=MaskType.choices, default=MaskType.CUSTOM)
    keep_head = models.PositiveSmallIntegerField(_("Keep head"), default=3)
    keep_tail = models.PositiveSmallIntegerField(_("Keep tail"), default=2)
    mask_char = models.CharField(_("Mask char"), max_length=4, default="*")
    pattern = models.CharField(_("Custom pattern"), max_length=255, blank=True, null=True)
    roles = models.ManyToManyField(
        to="system.UserRole", verbose_name=_("Roles"), blank=True, help_text=_("Empty = all non-superuser")
    )
    is_active = models.BooleanField(_("Is active"), default=True)
    sort = models.IntegerField(_("Sort"), default=0)

    class Meta:
        ordering = ("sort", "created_time")
        verbose_name = _("Data mask rule")
        indexes = [models.Index(fields=["model", "field"], name="idx_datamask_model_field")]

    def __str__(self):
        return f"{self.model}.{self.field}({self.mask_type})"
